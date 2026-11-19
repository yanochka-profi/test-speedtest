"""speedtest.py — замер скорости скачивания: N полных запросов к URL, скорость в битах."""
from __future__ import annotations

import argparse
import sys
import time
import urllib.error
import urllib.request

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; speedtest/1.0)",
    "Cache-Control": "no-cache",
}

CHUNK_SIZE = 1048576  # 1 MiB: накладные расходы цикла незначимы даже для 10G-линков


def fetch_once(url: str, timeout: float, chunk_size: int = CHUNK_SIZE) -> tuple[int, float]:
    """Скачивает URL целиком в стрим-буфер, ничего не сохраняя на диск.

    Возвращает (скачано_байт, затрачено_секунд). Сетевые и HTTP-ошибки
    пробрасывает как urllib.error.URLError / OSError.
    """
    req = urllib.request.Request(url, headers=HEADERS)
    started = time.perf_counter()
    total = 0
    resp = urllib.request.urlopen(req, timeout=timeout)
    try:
        declared = resp.headers.get("Content-Length")
        try:
            expected = int(declared) if declared is not None else None
        except ValueError:
            expected = None  # кривой заголовок — сравнивать нечем
        while True:
            chunk = resp.read(chunk_size)
            if not chunk:
                break
            total += len(chunk)
    finally:
        resp.close()
    if expected is not None and total != expected:
        raise OSError(f"соединение оборвалось: скачано {total} из {expected} байт")
    return total, time.perf_counter() - started


def format_size(num_bytes: int) -> str:
    """Объём в мегабайтах по SI: 1 МБ = 10^6 байт (так считают провайдеры и вендоры дисков)."""
    return f"{num_bytes / 1_000_000:.1f} MB"


def format_bitrate(bits_per_second: float) -> str:
    """Скорость в битах с SI-приставками: bit/s -> kbit/s -> Mbit/s -> Gbit/s."""
    if bits_per_second >= 1_000_000_000:
        return f"{bits_per_second / 1_000_000_000:.1f} Gbit/s"
    if bits_per_second >= 1_000_000:
        return f"{bits_per_second / 1_000_000:.1f} Mbit/s"
    if bits_per_second >= 1_000:
        return f"{bits_per_second / 1_000:.1f} kbit/s"
    return f"{bits_per_second:.1f} bit/s"


def _positive_int(value: str) -> int:
    ivalue = int(value)
    if ivalue < 1:
        raise argparse.ArgumentTypeError(f"ожидалось число >= 1, получено {value}")
    return ivalue


def _positive_float(value: str) -> float:
    fvalue = float(value)
    if fvalue <= 0:
        raise argparse.ArgumentTypeError(f"ожидалось положительное число секунд, получено {value}")
    return fvalue


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="speedtest.py",
        description="Замер скорости скачивания: N последовательных полных запросов к URL.",
    )
    parser.add_argument("url", help="адрес, к которому стучаться (например, ссылка на тяжёлую картинку)")
    parser.add_argument("-n", "--requests", type=_positive_int, default=10,
                        help="число последовательных запросов (по умолчанию: 10)")
    parser.add_argument("--timeout", type=_positive_float, default=60.0,
                        help="секунд бездействия: ожидание соединения и каждого нового "
                             "блока данных (по умолчанию: 60)")
    args = parser.parse_args(argv)

    print(f"Цель: {args.url}")
    print(f"Запросов: {args.requests}, таймаут на запрос: {args.timeout:g} c")

    sizes: list[int] = []
    times: list[float] = []
    for i in range(1, args.requests + 1):
        try:
            total, elapsed = fetch_once(args.url, args.timeout)
        except (urllib.error.URLError, OSError) as exc:
            print(f"speedtest: запрос {i}: {exc}", file=sys.stderr)
            return 1
        sizes.append(total)
        times.append(elapsed)
        print(f"[{i}/{args.requests}] {format_size(total)} | {elapsed:.2f} c | "
              f"{format_bitrate(total * 8 / elapsed)}")

    total_bytes = sum(sizes)
    total_time = sum(times)
    print()
    print(f"Среднее время запроса: {total_time / len(times):.2f} c")
    print(f"Всего скачано: {format_size(total_bytes)} ({total_bytes} байт)")
    print(f"Суммарное время: {total_time:.2f} c")
    print(f"Средняя скорость: {format_bitrate(total_bytes * 8 / total_time)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
