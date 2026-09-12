#!/usr/bin/env python3
"""Download and format the public traces consumed by ``reproduce.py``.

Cloudflare and Meta publish Zstandard-compressed CSV files in (or very close
to) the required layout.  Wikimedia publishes daily gzip-compressed TSV files;
those are projected to the simulator's three input columns and assembled as
concatenated Zstandard frames without materializing the much larger plain CSV.
Every final input is SHA-256 checked before it is accepted or reused.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


HERE = Path(__file__).resolve().parent
FORMAT_VERSION = 2
ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"

# SHA-256 digests of the exact final compressed inputs used by the artifact.
# The two Wikimedia inputs are deterministic assembled outputs; their hashes
# cover the final projected traces, while WM_SOURCE_SHA256 below pins every
# raw daily archive fetched from Wikimedia before projection.
TRACE_SHA256 = {
    "cdn_cf25_csv/106m105.csv.zst": "33426eb087ea8cfb028bd400a624a7f0a8e454b93d6e5bf5dad79bc917e6af97",
    "cdn_cf25_csv/106m106.csv.zst": "d1a6663b7ffa9bfb558d43b4c2e804cb098bbe64bef9f7ab639b2004a59a3660",
    "cdn_cf25_csv/243m12.csv.zst": "c5c8962b55f9e389a1be6d860870bd4cf2bdc8508ff7cc9bd6e0a91828c4338d",
    "cdn_cf25_csv/243m13.csv.zst": "9a27c5e21ff249b117a5163c40341472a2032fc3ab70bc408d05dd1af6329bdf",
    "cdn_cf25_csv/411m264.csv.zst": "fe9af6e70446ab34b72fd05827ee4aef231b3f4d666db2f75fe05dfcc94ea7b5",
    "cdn_cf25_csv/411m325.csv.zst": "262d87ed2fd955f07ae156e1a5f549870f5f9e6684ffbedef841ab85738fbcf8",
    "cdn_cf25_csv/472m378.csv.zst": "64249e8dcae013c3c1a452b36e4513a8696c6164f4abaea6acd928b01e089738",
    "cdn_cf25_csv/472m379.csv.zst": "046ca3512d9f0d824edddab754b9eb5acf8d35074618707070d3a5216ace017e",
    "cdn_fb23_csv/reag.csv.zst": "7e403d73893439b3551269039ff2747fe486afcd37b63d5cc1ed38ff719222ab",
    "cdn_fb23_csv/rhna.csv.zst": "4f9348c4076f2646b83b3e4dd6538f5aee7d4eee67509d5384f42102ce00d9c3",
    "cdn_fb23_csv/rprn.csv.zst": "c2fbafac5c4387ffb9bffef4e47945971ad15df3e566a18a97ca411840a121e9",
    "cdn_wm19_csv/t-all.csv.zst": "579a106228124b9ee922e2186a58e603e4788ad1084459d457c566c53f2cd273",
    "cdn_wm19_csv/u-all.csv.zst": "d4b60e544d087ccac9e60d03e1dc16542dc17682a706cbff4cd97537877af536",
}


@dataclass(frozen=True)
class DirectTrace:
    dataset: str
    url: str
    relative_output: str
    expected_header: bytes


CF_BASE = "https://objects.research.cloudflare.com/@ikhor/cdn-traces"
CF_HEADER = b"timestamp,key,zone,size,expiry_time,stale_time,method,mime"
CF_FILES = (
    "106m105.csv.zst", "106m106.csv.zst", "243m12.csv.zst", "243m13.csv.zst",
    "411m264.csv.zst", "411m325.csv.zst", "472m378.csv.zst", "472m379.csv.zst",
)

META_BASE = "https://s3.amazonaws.com/cache-datasets/cache_dataset_txt/2023_metaCDN"
META_HEADER = (
    b"timestamp,cacheKey,OpType,objectSize,responseSize,responseHeaderSize,"
    b"rangeStart,rangeEnd,TTL,SamplingRate,cache_hit,item_value,RequestHandler,"
    b"cdn_content_type_id,vip_type"
)
META_FILES = (
    ("reag0c01_20230315_20230322_0.2000.csv.zst", "reag.csv.zst"),
    # The paper's local trace name is ``rhna``; Meta's published object is ``rnha``.
    ("rnha0c01_20230315_20230322_0.8000.csv.zst", "rhna.csv.zst"),
    ("rprn0c01_20230315_20230322_0.2000.csv.zst", "rprn.csv.zst"),
)

DIRECT_TRACES = tuple(
    DirectTrace(
        "cf", f"{CF_BASE}/{name}", f"cdn_cf25_csv/{name}", CF_HEADER
    )
    for name in CF_FILES
) + tuple(
    DirectTrace(
        "fb", f"{META_BASE}/{remote}", f"cdn_fb23_csv/{local}", META_HEADER
    )
    for remote, local in META_FILES
)


@dataclass(frozen=True)
class WikimediaTrace:
    name: str
    directory: str
    days: tuple[int, ...]
    source_header: bytes
    fields: str

    @property
    def output_name(self) -> str:
        return f"{self.name}-all.csv.zst"


WM_BASE = "https://analytics.wikimedia.org/published/datasets/caching/2019"
WM_EXISTING_SIZES = {
    "t-all.csv.zst": 1_867_440_658,
    "u-all.csv.zst": 22_950_936_199,
}

# SHA-256 digests of the raw Wikimedia daily archives (as published) keyed by
# their "<directory>/<file>" path under WM_BASE. These pin every source input
# fetched by fetch_wikimedia before it is projected and assembled.
WM_SOURCE_SHA256 = {
    "text/cache-t-01.gz": "0ea0bd7836353a3d157a10b3baa57e810e4f3360aeb1da18948abd0844991947",
    "text/cache-t-02.gz": "85fc7d1d7a25576aafbb3bc2dfc090fc63ef8eb8052bd800a65242a3690df989",
    "text/cache-t-03.gz": "8712f65138e38c86bf6db8fd21aa2ee48eb4e828186b7b89b3dc7312d64c12dc",
    "text/cache-t-04.gz": "6e81724b31400fd7ac0d5c3228b5c63573201ebab8c544426f51a88980c3a178",
    "text/cache-t-05.gz": "a5584febba41ed046194b3ef019c6098f1cabfa58be912875e5687647c2278eb",
    "text/cache-t-06.gz": "30d73f40dcf9bb1350a0622970781d90a132530fd0850ef11a6ee4d9e0e87c30",
    "text/cache-t-07.gz": "063adecaea0b1bc33e760984f2a51058655b687b0688fe9d951d7d39e8ae63cd",
    "text/cache-t-08.gz": "dc2e020e1c833d773730b94eec7d4a38b00a40058ee26f4e66c5c7f58f741dab",
    "text/cache-t-09.gz": "073dbf8f8887138c1d7b32d219451aef2f0797cef4f076e5ca06b26c0b351318",
    "text/cache-t-10.gz": "066e8fb99448b1888fd3dad6d5a12d71de10980cd03a44c71c9c07075d071533",
    "text/cache-t-11.gz": "fb033fb036364d13b8fc1e4377a82d8249046d9dd7bf56722ffcde32cc446cf3",
    "text/cache-t-12.gz": "435ae148a6f49e367c08b0227daa49a5e6d0e3e7915effd1047bb34119767200",
    "text/cache-t-13.gz": "680f629f1b46d5325021d5272c32e1d2f761260d1cb49a559664881a0c8713fa",
    "text/cache-t-14.gz": "a1b0b596872a85c17a83433c8b6acb85cd3369c66ee5be0b472d43248e267a53",
    "text/cache-t-15.gz": "925acb45f3656ab001566c85077f77d11b8be0990a963928cc1d72cb60f0dd60",
    "text/cache-t-16.gz": "b154b9afaf02c28623c8ae26560c56450912fc3fedc43e6bb912f18e27c450ef",
    "text/cache-t-17.gz": "ee65cd5203e3fca9e88f7c953615cfb5789bbd8cbad180d3a85ad1cb05ea1612",
    "text/cache-t-18.gz": "f1dafe9ebdf9789d634f1fa968d960188ba89d2c5a5dc07e96e730b142016f9f",
    "text/cache-t-19.gz": "245bfebf0382513452a803d3bab0a8816bcf8d01cf93cc2b1b22d19ad72ee272",
    "text/cache-t-20.gz": "fec163768d86658a3aed6fe82c7fbf1bed99de2dfcd8c9518bfb17e7f255537e",
    "upload/cache-u-00.gz": "06fd6af9eb65a104a8c10a11feddbc0963f3a993145b7c02ad4349a5d77a0c7f",
    "upload/cache-u-01.gz": "18d5ebd02d93832380acee7a2ba08011a3e20f9c9f018f1a420f350d723e252d",
    "upload/cache-u-02.gz": "f5b9568466c3ba374152260790c0bbc856ab96e293fbc43c8140c8c602a68f38",
    "upload/cache-u-03.gz": "27095432ff539295c4e701753f2346fa5a5b7a7a971884b09929cacd1f39dbbd",
    "upload/cache-u-04.gz": "6947e59dce4486aef028625e528dc0c57c2f21cb78d1340e5d338fddf578abe4",
    "upload/cache-u-05.gz": "d11d1fb1da9b324616bf31dd24489d656074efe23ee7ada1ec6a8ecc03080636",
    "upload/cache-u-06.gz": "1f91ab29c25a52c5033616485d33bea9ffbd5e180b539efb4fb68b1963e0647a",
    "upload/cache-u-07.gz": "7d0f7e6781b5fd9e6ddd9d6578c195c849681897ef9797e36391dafa5f59df2c",
    "upload/cache-u-08.gz": "8c88b5d3f9adfa4c4d646165fb7fb1dc4bdefc9939639dec8b339094b4e625f5",
    "upload/cache-u-09.gz": "4160d37463aa83ae666cc1612daa23465b38bfe88d8253273356c9831100cf34",
    "upload/cache-u-10.gz": "243c45b510d1a8b0284de79c6df800c008a6208b1074401fc2933719a596b8ca",
    "upload/cache-u-11.gz": "7ab624b3526c70d2292f8800ddad0dfa238f7979850bd5afc746574c65f6d9aa",
    "upload/cache-u-12.gz": "e4cdbf78a748a78b016ef928a9c7bccd975142715f6c9369fc31b46c1bdeb6fc",
    "upload/cache-u-13.gz": "eb6e6fffc0d95751ac96d9e7159ffc9df30c5328f98bd413c2fa862e31d633b7",
    "upload/cache-u-14.gz": "0006a25fd3af5718b813dca13c55b4ffdeca0eddf33cee105aefbab3b974f90c",
    "upload/cache-u-15.gz": "6f8650ee51bd4de8eb0d6607c23bdb5278b16f1570b67e919b4b4a04c4f32da0",
    "upload/cache-u-16.gz": "f0f3bdd058b023b89d751858af4a3aa6f476fbf1d1e9f75b231feb1f4e81642e",
    "upload/cache-u-17.gz": "97a97a1fbbd2a62c58600ea3cb70c37b01d17642779b1abc9d9b8888b0f4b0ca",
    "upload/cache-u-18.gz": "2bc54662c574f9641e87ff0b1bd3a5a4e2eb68ce34d230a461617ca6b7177fb0",
    "upload/cache-u-19.gz": "e2c7a954f47a817a54e0a8870e69313a69ea3175676589f235680c35751cb327",
    "upload/cache-u-20.gz": "5979240c9571c5fc6046bcbca937eb83daaeb3e32908e2aee2146f8bd2c3575d",
}
WM_TRACES = (
    # The committed experiment's wm_t input spans timestamps 86400..1814399
    # and 197,819,321 requests, corresponding to published text days 01--20.
    WikimediaTrace(
        "t", "text", tuple(range(1, 21)),
        b"relative_unix\thashed_host_path_query\tresponse_size\ttime_firstbyte",
        "1-3",
    ),
    WikimediaTrace(
        "u", "upload", tuple(range(21)),
        b"relative_unix\thashed_path_query\timage_type\tresponse_size\ttime_firstbyte",
        "1,2,4",
    ),
)


class Downloader:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.trace_root = args.trace_root.expanduser().resolve()
        if self.trace_root == Path(self.trace_root.anchor):
            raise ValueError("refusing to use a filesystem root as --trace-root")
        self.stage = self.trace_root / ".reproduction-downloads"

    def announce(self, *parts: object) -> None:
        print(" ".join(str(part) for part in parts), flush=True)

    def command(self, command: Sequence[object]) -> None:
        self.announce("$", shlex.join(str(part) for part in command))

    def require_tools(self, selected: set[str], *, downloading: bool = True) -> None:
        required = ["zstd"]
        if downloading:
            required.append("curl")
        if downloading and "wm" in selected:
            required.extend(("gzip", "tail", "cut"))
        missing = [
            name for name in required if shutil.which(name) is None
        ]
        if missing:
            raise RuntimeError("missing required command(s): " + ", ".join(missing))

    def marker_path(self, output: Path) -> Path:
        safe = str(output.relative_to(self.trace_root)).replace("/", "__")
        return self.stage / "complete" / f"{safe}.json"

    def expected_sha256(self, output: Path) -> str:
        relative = output.relative_to(self.trace_root).as_posix()
        try:
            return TRACE_SHA256[relative]
        except KeyError as error:
            raise RuntimeError(f"no checksum is recorded for {relative}") from error

    @staticmethod
    def sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def validate_sha256(self, path: Path, output: Path) -> str:
        expected = self.expected_sha256(output)
        actual = self.sha256(path)
        if actual != expected:
            raise RuntimeError(
                f"SHA-256 mismatch for {path}: expected {expected}, got {actual}"
            )
        return actual

    def marker_matches(self, output: Path, sources: Sequence[str]) -> bool:
        try:
            record = json.loads(self.marker_path(output).read_text(encoding="utf-8"))
            stat = output.stat()
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return False
        return (
            record.get("format_version") == FORMAT_VERSION
            and record.get("sources") == list(sources)
            and record.get("bytes") == stat.st_size
            and record.get("sha256") == self.expected_sha256(output)
            and stat.st_size > 0
        )

    def mark_complete(
        self, output: Path, sources: Sequence[str], checksum: str | None = None,
    ) -> None:
        marker = self.marker_path(output)
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(json.dumps({
            "format_version": FORMAT_VERSION,
            "sources": list(sources),
            "output": str(output),
            "bytes": output.stat().st_size,
            "sha256": checksum or self.validate_sha256(output, output),
        }, indent=2) + "\n", encoding="utf-8")

    def zstd_test(self, path: Path) -> None:
        subprocess.run(("zstd", "-tq", path), check=True)

    def zstd_header(self, path: Path) -> bytes:
        process = subprocess.Popen(
            ("zstd", "-dcq", path), stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        assert process.stdout is not None
        try:
            return process.stdout.readline().rstrip(b"\r\n")
        finally:
            process.stdout.close()
            process.terminate()
            process.wait()

    def download(self, url: str, destination: Path, compression: str) -> None:
        part = destination.with_name(destination.name + ".part")
        command = (
            "curl", "--fail", "--location", "--retry", "10",
            "--retry-all-errors", "--continue-at", "-", "--output", part, url,
        )
        self.command(command)
        if self.args.dry_run:
            return
        destination.parent.mkdir(parents=True, exist_ok=True)
        completed = subprocess.run(command, check=False)
        if completed.returncode:
            # A resumed request for an already complete .part file can receive
            # HTTP 416. Accept it only if the complete-file test succeeds.
            if not self.compression_valid(part, compression):
                if completed.returncode != 33:  # CURLE_RANGE_ERROR
                    completed.check_returncode()
                # Some publishers do not honor Range requests. Restart an
                # incomplete object once when continuation is unavailable.
                part.unlink(missing_ok=True)
                restart = tuple(item for item in command if item not in ("--continue-at", "-"))
                self.command(restart)
                subprocess.run(restart, check=True)
        if not self.compression_valid(part, compression):
            raise RuntimeError(f"downloaded file failed {compression} validation: {part}")
        os.replace(part, destination)

    def compression_valid(self, path: Path, compression: str) -> bool:
        if not path.is_file() or path.stat().st_size == 0:
            return False
        command = ("zstd", "-tq", path) if compression == "zstd" else ("gzip", "-t", path)
        return subprocess.run(command, stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL).returncode == 0

    def direct_trace_complete(
        self, spec: DirectTrace, output: Path, *, mark: bool = True,
    ) -> bool:
        if self.args.force or not output.is_file():
            return False
        # Schema plus frame magic is stable across harmless publisher-side
        # recompression, while the pinned SHA-256 proves the exact input.
        with output.open("rb") as stream:
            magic = stream.read(4)
        if magic != ZSTD_MAGIC or self.zstd_header(output) != spec.expected_header:
            return False
        checksum = self.validate_sha256(output, output)
        if self.args.verify_existing:
            self.zstd_test(output)
        if mark and not self.marker_matches(output, (spec.url,)):
            self.mark_complete(output, (spec.url,), checksum)
        return True

    def fetch_direct(self, spec: DirectTrace) -> None:
        output = self.trace_root / spec.relative_output
        if self.args.dry_run:
            self.announce(f"prepare {spec.dataset}: {spec.url} -> {output}")
            self.download(spec.url, output, "zstd")
            return
        if self.direct_trace_complete(spec, output):
            self.announce("already complete", output)
            return
        output.parent.mkdir(parents=True, exist_ok=True)
        staged = self.stage / "direct" / Path(spec.relative_output).name
        self.download(spec.url, staged, "zstd")
        if self.zstd_header(staged) != spec.expected_header:
            raise RuntimeError(f"unexpected CSV header in {spec.url}")
        checksum = self.validate_sha256(staged, output)
        os.replace(staged, output)
        self.mark_complete(output, (spec.url,), checksum)
        self.announce("completed", output)

    def wm_url(self, spec: WikimediaTrace, day: int) -> str:
        return f"{WM_BASE}/{spec.directory}/cache-{spec.name}-{day:02d}.gz"

    def wm_source_key(self, spec: WikimediaTrace, day: int) -> str:
        return f"{spec.directory}/cache-{spec.name}-{day:02d}.gz"

    def expected_source_sha256(self, key: str) -> str:
        try:
            return WM_SOURCE_SHA256[key]
        except KeyError as error:
            raise RuntimeError(f"no source checksum is recorded for {key}") from error

    def validate_source_sha256(self, path: Path, key: str) -> str:
        expected = self.expected_source_sha256(key)
        actual = self.sha256(path)
        if actual != expected:
            raise RuntimeError(
                f"SHA-256 mismatch for {key}: expected {expected}, got {actual}"
            )
        return actual

    def format_wikimedia_day(
        self, raw: Path, fragment: Path, spec: WikimediaTrace,
    ) -> None:
        with gzip.open(raw, "rb") as stream:
            header = stream.readline().rstrip(b"\r\n")
        if header != spec.source_header:
            raise RuntimeError(f"unexpected Wikimedia header in {raw}: {header!r}")

        part = fragment.with_name(fragment.name + ".part")
        part.parent.mkdir(parents=True, exist_ok=True)
        commands = (
            ("gzip", "-dc", raw),
            ("tail", "-n", "+2"),
            ("cut", "-f", spec.fields, "--output-delimiter=,"),
            ("zstd", "-T0", "-q", "-c"),
        )
        self.command((*commands[0], "|", *commands[1], "|", *commands[2], "|",
                      *commands[3], ">", part))
        with part.open("wb") as output:
            first = subprocess.Popen(commands[0], stdout=subprocess.PIPE)
            assert first.stdout is not None
            second = subprocess.Popen(commands[1], stdin=first.stdout, stdout=subprocess.PIPE)
            first.stdout.close()
            assert second.stdout is not None
            third = subprocess.Popen(commands[2], stdin=second.stdout, stdout=subprocess.PIPE)
            second.stdout.close()
            assert third.stdout is not None
            fourth = subprocess.Popen(commands[3], stdin=third.stdout, stdout=output)
            third.stdout.close()
            statuses = (fourth.wait(), third.wait(), second.wait(), first.wait())
        if any(statuses):
            part.unlink(missing_ok=True)
            raise RuntimeError(f"Wikimedia conversion failed for {raw}: {statuses}")
        self.zstd_test(part)
        os.replace(part, fragment)

    def assemble_wikimedia(
        self, output: Path, fragments: Sequence[Path], sources: Sequence[str],
    ) -> None:
        part = output.with_name(output.name + ".part")
        output.parent.mkdir(parents=True, exist_ok=True)
        with part.open("wb") as destination:
            subprocess.run(
                ("zstd", "-q", "-c"), input=b"timestamp,key,size\n",
                stdout=destination, check=True,
            )
            for fragment in fragments:
                with fragment.open("rb") as source:
                    shutil.copyfileobj(source, destination, length=8 * 1024 * 1024)
        self.zstd_test(part)
        if self.zstd_header(part) != b"timestamp,key,size":
            part.unlink(missing_ok=True)
            raise RuntimeError(f"failed to assemble {output}")
        checksum = self.validate_sha256(part, output)
        os.replace(part, output)
        self.mark_complete(output, sources, checksum)

    def fetch_wikimedia(self, spec: WikimediaTrace) -> None:
        output = self.trace_root / "cdn_wm19_csv" / spec.output_name
        sources = tuple(self.wm_url(spec, day) for day in spec.days)
        if self.args.dry_run:
            self.announce(
                f"prepare wm_{spec.name}: {len(spec.days)} daily TSV archives -> {output}"
            )
            for day, url in zip(spec.days, sources, strict=True):
                raw = self.stage / "wikimedia" / f"cache-{spec.name}-{day:02d}.gz"
                self.download(url, raw, "gzip")
            return
        if not self.args.force and self.wikimedia_trace_complete(
            spec, output, sources,
        ):
            self.announce("already complete", output)
            return
        self.announce(
            f"prepare wm_{spec.name}: {len(spec.days)} daily TSV archives -> {output}"
        )
        fragments = []
        for day, url in zip(spec.days, sources, strict=True):
            stem = f"cache-{spec.name}-{day:02d}"
            raw = self.stage / "wikimedia" / f"{stem}.gz"
            fragment = self.stage / "wikimedia" / f"{stem}.csv.zst"
            fragments.append(fragment)
            if not self.compression_valid(fragment, "zstd"):
                if not self.compression_valid(raw, "gzip"):
                    self.download(url, raw, "gzip")
                self.validate_source_sha256(raw, self.wm_source_key(spec, day))
                self.format_wikimedia_day(raw, fragment, spec)
            else:
                self.announce("reuse formatted day", fragment)
            if not self.args.keep_downloads:
                raw.unlink(missing_ok=True)

        self.assemble_wikimedia(output, fragments, sources)
        if not self.args.keep_downloads:
            for fragment in fragments:
                fragment.unlink(missing_ok=True)
        self.announce("completed", output)

    def wikimedia_trace_complete(
        self, spec: WikimediaTrace, output: Path, sources: Sequence[str], *, mark: bool = True,
    ) -> bool:
        if not output.is_file():
            return False
        with output.open("rb") as stream:
            magic = stream.read(4)
        known_size = WM_EXISTING_SIZES.get(output.name)
        if (
            output.stat().st_size != known_size
            or magic != ZSTD_MAGIC
            or self.zstd_header(output) != b"timestamp,key,size"
        ):
            return False
        checksum = self.validate_sha256(output, output)
        if self.args.verify_existing:
            self.zstd_test(output)
        if mark and not self.marker_matches(output, sources):
            self.mark_complete(output, sources, checksum)
        return True

    def check_existing(self, selected: set[str]) -> None:
        for spec in DIRECT_TRACES:
            if spec.dataset not in selected:
                continue
            output = self.trace_root / spec.relative_output
            if not self.direct_trace_complete(spec, output, mark=False):
                raise FileNotFoundError(f"missing or invalid trace: {output}")
            self.announce("checksum verified", output)
        if "wm" in selected:
            for spec in WM_TRACES:
                output = self.trace_root / "cdn_wm19_csv" / spec.output_name
                sources = tuple(self.wm_url(spec, day) for day in spec.days)
                if not self.wikimedia_trace_complete(spec, output, sources, mark=False):
                    raise FileNotFoundError(f"missing or invalid trace: {output}")
                self.announce("checksum verified", output)

    def run(self) -> None:
        selected = set(self.args.dataset or ("cf", "fb", "wm"))
        if self.args.check:
            self.require_tools(selected, downloading=False)
            self.check_existing(selected)
            self.announce("trace checksum verification complete for", ", ".join(sorted(selected)))
            return
        if not self.args.dry_run:
            self.require_tools(selected)
        for spec in DIRECT_TRACES:
            if spec.dataset in selected:
                self.fetch_direct(spec)
        if "wm" in selected:
            for spec in WM_TRACES:
                self.fetch_wikimedia(spec)
        self.announce("trace preparation complete for", ", ".join(sorted(selected)))


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--trace-root", type=Path, default=HERE / "traces",
        help="destination trace root (default: artifact/traces/)",
    )
    parser.add_argument(
        "--dataset", choices=("cf", "fb", "wm"), action="append",
        help="prepare only this dataset; repeat as needed (default: all)",
    )
    parser.add_argument(
        "--keep-downloads", action="store_true",
        help="retain Wikimedia gzip archives and formatted daily fragments",
    )
    parser.add_argument(
        "--verify-existing", action="store_true",
        help="fully decompress-test existing direct-download traces",
    )
    parser.add_argument(
        "--check", action="store_true",
        help="verify existing final trace checksums without downloading or writing",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="replace final trace files even when completion metadata matches",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="print URLs, destinations, and commands without downloading or writing",
    )
    args = parser.parse_args(argv)
    if args.check and (args.force or args.keep_downloads):
        parser.error("--check cannot be combined with --force or --keep-downloads")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    try:
        Downloader(parse_args(argv)).run()
    except (OSError, RuntimeError, ValueError, subprocess.CalledProcessError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
