"""
Ran CPU-bound gazette parsing (pdfplumber/PyMuPDF over a 600+ page MSBTE
gazette, 40-60+ seconds of near-100% CPU) in a ProcessPoolExecutor for a
while, to stop it from starving the asyncio event loop's ability to answer
other requests (e.g. a client's own course-catalogue polling GET) while a
parse was in flight -- a real problem, verified directly against a local
server.

That traded one failure mode for a worse one on Render's free tier: a
ProcessPoolExecutor worker is a second, fully separate Python interpreter,
which duplicates a meaningful chunk of memory on top of the main process
(the raw PDF bytes, plus pdfplumber's per-page parse buffers) rather than
sharing it the way a thread would. Render's free tier has ~512MB total, and
production logs showed the instance getting killed and cold-restarted by
the platform mid-parse (a clean "Started server process" / "Application
startup complete" sequence appearing in the middle of what should have been
an uninterrupted polling sequence) -- the signature of an out-of-memory
kill, not a bug in the parsing logic itself.

A full restart mid-request is a worse failure than the event-loop-starvation
problem this was meant to fix (it drops every in-flight request, not just
the polling one, and wipes all in-memory job/upload state), so this reverts
to running the work as a plain function -- still off the async request
handler (still called from inside a BackgroundTasks callback, which
Starlette runs via run_in_threadpool, i.e. a real thread, not blocking the
event loop directly) but without a second process's memory overhead.

If a future upgrade to a Render plan with real memory headroom (well above
512MB) makes the OOM risk moot, the process-pool version is worth
reinstating for its event-loop-responsiveness benefit -- see git history
for that implementation.
"""
from typing import Callable, TypeVar

T = TypeVar("T")


def run_cpu_bound(fn: Callable[..., T], *args) -> T:
    """Runs fn(*args) and returns its result. Historically routed through a
    process pool (see module docstring for why that was reverted) -- kept
    as a named wrapper so call sites don't need to change again if a future
    fix reintroduces process isolation once memory headroom allows it."""
    return fn(*args)
