"""CLI workers share recorded spending, including workers started before another process writes."""
import json
import multiprocessing
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from clocktower.llm import BudgetExceeded, Ledger


def spend_in_process(path, barrier, results):
    ledger = Ledger(path, cap=1)
    barrier.wait(timeout=20)  # every worker starts with the same empty ledger
    for _ in range(2):
        ledger.add(.25, label='worker')
    barrier.wait(timeout=20)
    try:
        ledger.check()
    except BudgetExceeded:
        results.put(ledger.spent)
    else:
        results.put(None)


class CLILedgerTests(unittest.TestCase):
    def test_existing_workers_see_each_others_spending(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'ledger.jsonl'
            a, b = Ledger(path, cap=1), Ledger(path, cap=1)
            a.add(.6)
            b.check()
            self.assertEqual(b.spent, .6)
            b.add(.6)  # already-started requests are recorded even when they cross the threshold
            for ledger in (a, b, Ledger(path, cap=1)):
                with self.assertRaises(BudgetExceeded):
                    ledger.check()
                self.assertAlmostEqual(ledger.spent, 1.2)

    def test_parallel_processes_stop_at_the_shared_recorded_total(self):
        ctx = multiprocessing.get_context('spawn')
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'ledger.jsonl'
            barrier, results = ctx.Barrier(4), ctx.Queue()
            workers = [ctx.Process(target=spend_in_process, args=(path, barrier, results)) for _ in range(4)]
            try:
                for p in workers:
                    p.start()
                self.assertEqual([results.get(timeout=30) for _ in workers], [2.0] * 4)
                for p in workers:
                    p.join(timeout=10)
                    self.assertEqual(p.exitcode, 0)
                rows = [json.loads(line) for line in path.read_text().splitlines()]
                self.assertEqual(len(rows), 8)
                self.assertEqual(sum(r['cost'] for r in rows), 2)
                self.assertEqual(Ledger(path).spent, 2)
            finally:
                for p in workers:
                    if p.is_alive():
                        p.terminate()
                        p.join(timeout=10)
                results.close()
                results.join_thread()

    def test_threads_do_not_double_count_or_lose_appends(self):
        with tempfile.TemporaryDirectory() as d:
            ledger = Ledger(Path(d) / 'ledger.jsonl')
            def spend(_):
                ledger.add(.25)
                ledger.check()
            with ThreadPoolExecutor(max_workers=8) as pool:
                list(pool.map(spend, range(80)))
            self.assertEqual(ledger.spent, 20)
            self.assertEqual(Ledger(ledger.path).spent, 20)

    def test_existing_jsonl_is_counted_once_and_corruption_blocks_checks(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'ledger.jsonl'
            path.write_text('{"cost": 2}\n\n{"cost": 3, "label": "old"}\n')
            ledger = Ledger(path, cap=10)
            ledger.check()
            ledger.check()
            self.assertEqual(ledger.spent, 5)
            with path.open('a') as f:
                f.write('{"cost":')
            with self.assertRaises(json.JSONDecodeError):
                ledger.check()
