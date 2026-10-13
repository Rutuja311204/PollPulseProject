"""
PollPulse - Threading demonstration (Unit 3)

THIS IS A SEPARATE LEARNING DEMO. The web application does NOT use threads.
Run it from the project folder:

    python demos/threading_demo.py

What it does
------------
1. Generates 60,000 fake survey responses in memory (no database needed).
2. Counts the votes for each option in two ways:
     a) sequentially, in the main thread
     b) with 4 worker threads, each counting one chunk of the responses
3. Merges the threads' partial results safely using a Lock and checks
   that both methods give exactly the same counts.

Key ideas for the viva
----------------------
* A THREAD is an independent path of execution inside one process.
  All threads of a process share the same memory (variables, objects).
* Shared data must be protected. Here every thread writes into its OWN
  Counter, and only the final merge into the shared total uses a Lock,
  so two threads never update the shared Counter at the same moment.
* Python's Global Interpreter Lock (GIL) lets only one thread run Python
  bytecode at a time. So for CPU-bound work like counting, threads are
  NOT faster - you will usually see similar or slightly slower times.
  Threads help with I/O-bound work (waiting for network, disk, database),
  because a waiting thread releases the GIL.
* Why the web app does not need threads: the web server already handles
  each request separately, a survey submission is a single quick database
  insert, and MongoDB itself handles concurrent writes safely (the unique
  index blocks duplicates). Adding background threads would only add
  complexity and risk.
"""
import random
import threading
import time
from collections import Counter

OPTIONS = ["Python", "Java", "C++", "JavaScript"]
WEIGHTS = [5, 3, 2, 3]
NUM_RESPONSES = 60_000
NUM_THREADS = 4


def generate_responses(n, seed=15):
    """Fake response documents with the same shape as the real ones."""
    rng = random.Random(seed)
    return [
        {"respondent_id": f"r{i}", "answers": [{"qid": "q_lang", "oid": rng.choices(OPTIONS, WEIGHTS)[0]}]}
        for i in range(n)
    ]


def count_votes(responses):
    """Count how many times each option was chosen."""
    counts = Counter()
    for response in responses:
        for answer in response["answers"]:
            counts[answer["oid"]] += 1
    return counts


class VoteCounterThread(threading.Thread):
    """A thread that counts one chunk of responses and merges the result
    into a shared Counter, protected by a Lock."""

    def __init__(self, name, chunk, shared_total, lock):
        super().__init__(name=name)
        self.chunk = chunk
        self.shared_total = shared_total
        self.lock = lock
        self.local_count = 0

    def run(self):
        partial = count_votes(self.chunk)          # private work, no lock needed
        self.local_count = sum(partial.values())
        with self.lock:                             # only one thread merges at a time
            self.shared_total.update(partial)
        print(f"  {self.name} counted {self.local_count:,} answers")


def split_into_chunks(items, parts):
    """Split a list into `parts` nearly equal slices."""
    size = (len(items) + parts - 1) // parts
    return [items[i:i + size] for i in range(0, len(items), size)]


def percentages(counts):
    total = sum(counts.values())
    if total == 0:
        return {k: 0.0 for k in counts}
    return {k: round(v * 100 / total, 1) for k, v in counts.items()}


def main():
    print(f"Generating {NUM_RESPONSES:,} sample responses...")
    responses = generate_responses(NUM_RESPONSES)

    print("\n1) Sequential counting (main thread only)")
    start = time.perf_counter()
    sequential = count_votes(responses)
    seq_time = time.perf_counter() - start
    print(f"   done in {seq_time * 1000:.1f} ms")

    print(f"\n2) Counting with {NUM_THREADS} threads")
    shared_total = Counter()
    lock = threading.Lock()
    threads = [
        VoteCounterThread(f"Worker-{i + 1}", chunk, shared_total, lock)
        for i, chunk in enumerate(split_into_chunks(responses, NUM_THREADS))
    ]
    start = time.perf_counter()
    for t in threads:
        t.start()        # begin running run() in parallel
    for t in threads:
        t.join()         # wait until every thread has finished
    thread_time = time.perf_counter() - start
    print(f"   done in {thread_time * 1000:.1f} ms")

    print("\nResults")
    pct = percentages(shared_total)
    for option in OPTIONS:
        print(f"   {option:<11} {shared_total[option]:>6,} votes  {pct[option]:>5}%")

    print("\nSame result both ways?", "YES" if sequential == shared_total else "NO")
    print(
        "Note: because of Python's GIL, threads do not speed up CPU-bound counting.\n"
        "They are useful when work spends time waiting (network, disk, database)."
    )


if __name__ == "__main__":
    main()
