import random
import signal
import time
from concurrent.futures import ProcessPoolExecutor

from guessless.core import *  # pyright: ignore[reportWildcardImportFromLibrary]

type Configuration = tuple[tuple[int, int], int]  # ((width, height), n_mines)
type BenchmarkResult = tuple[float, int]  # (time, clicks)

TIMEOUT = 60 # seconds

configurations = [
    ((9, 9), 10),
    ((16, 16), 40),
    ((30, 16), 99),
]

def _timeout_handler(signum, frame):
    raise TimeoutError("Benchmark timed out")

def _benchmark(configuration: Configuration) -> BenchmarkResult:
    result: BenchmarkResult = (float("-inf"), 0)
    signal.signal(signal.SIGALRM, _timeout_handler)
    try:
        signal.setitimer(signal.ITIMER_REAL, TIMEOUT)
        t0 = time.perf_counter()
        game = Minesweeper(size=Size(*configuration[0]), n_mines=configuration[1])
        clicks = 0
        while game.state != GameState.WON:
            if game.state == GameState.LOST:
                result = (float("inf"), clicks)
                break
            if game.state == GameState.NOTSTARTED:
                pos = Position(random.randint(0, game.size.w - 1), random.randint(0, game.size.h - 1))
                game.reveal_at(pos)
            else:
                game.solve()
                if game.last_solve_result is None:
                    result = (float("inf"), clicks)
                    break
                if game.last_solve_result.must_be_safe:
                    pos = random.choice([*game.last_solve_result.must_be_safe])
                    game.reveal_at(pos, skip_solve=True)
                    clicks += 1
                elif game.last_solve_result.cannot_deter:
                    pos = random.choice([*game.last_solve_result.cannot_deter])
                    game.reveal_at(pos, skip_solve=True)
                    clicks += 1
                else:
                    result = (float("inf"), clicks)
                    break
        else:
            result = (time.perf_counter() - t0, clicks)
    except TimeoutError:
        result = (float("-inf"), 0)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
    return result

if __name__ == "__main__":
    print("|configuration|mean|max|min|errs|timeouts|time per click|")
    print("|---|---|---|---|---|---|---|")
    for configuration in configurations:
        with ProcessPoolExecutor(16) as ex:
            times = list(ex.map(_benchmark, [configuration] * 10))
        errs = sum(1 for t in times if t[0] == float("inf"))
        timeouts = sum(1 for t in times if t[0] == float("-inf"))
        normal_times = [t for t in times if t[0] not in (float("inf"), float("-inf"))]
        if normal_times:
            mean = sum(t[0] for t in normal_times) / len(normal_times)
            maximum = max(t[0] for t in normal_times)
            minimum = min(t[0] for t in normal_times)
            time_per_click = sum(t[0] / t[1] for t in normal_times) / len(normal_times)
        else:
            mean = maximum = minimum = time_per_click = float("nan")
        print(f"|{configuration}|{mean:.3f}s|{maximum:.3f}s|{minimum:.3f}s|{errs}/10|{timeouts}/10|{time_per_click:.3f}s/click|")
