"""
Shared process pool for genuinely CPU-bound work -- gazette PDF parsing is
the case that matters here: a real 600+ page MSBTE gazette takes 40-60+
seconds of near-100% CPU under pdfplumber/PyMuPDF (measured directly).

Running that as a plain function via FastAPI's BackgroundTasks still means
it executes on a *thread* (Starlette wraps sync background callables in
run_in_threadpool). A CPU-bound thread like that still competes with the
main event loop thread for the same GIL and the same physical CPU, and C
extensions like PyMuPDF don't reliably release the GIL during long internal
calls -- so in practice the event loop thread (the one answering every other
HTTP request, including a client's course-catalogue polling GET) got starved
for the whole 40-60s duration of a parse. On Render's free tier, where the
instance already only gets a sliver of shared CPU, that was enough for even
a trivial polling request to blow past Render's own gateway timeout and get
its connection dropped -- which, because the drop happens at the platform's
proxy layer rather than inside the app, carries no CORS headers, so the
browser reports it as an undiagnosable "couldn't reach the server" network
error instead of a real HTTP error or timeout.

A ProcessPoolExecutor runs the work in a genuinely separate OS process, so
the OS scheduler -- not the GIL, not however cooperative some C extension
feels like being -- decides how CPU time is split between it and the main
process. That's what actually keeps the app responsive to other requests
while a parse is running, even on a single shared vCPU.

Only pass picklable arguments and return values across it: raw bytes and
plain dicts/lists/strings, which is everything the gazette pipeline
functions already use.
"""
from concurrent.futures import ProcessPoolExecutor
from typing import Callable, TypeVar

T = TypeVar("T")

# max_workers=1: Render's free tier has one shared vCPU and ~512MB RAM.  A
# second concurrent worker would just contend for the same CPU anyway, and
# each one is a whole forked Python interpreter's worth of memory this
# instance doesn't have to spare.
_executor = ProcessPoolExecutor(max_workers=1)


def run_cpu_bound(fn: Callable[..., T], *args) -> T:
    """Runs fn(*args) in the shared process pool and blocks the *calling*
    thread (not the asyncio event loop) until it's done. Call this from
    inside a BackgroundTasks callback or other already-threaded context --
    never directly from an async request handler."""
    return _executor.submit(fn, *args).result()
