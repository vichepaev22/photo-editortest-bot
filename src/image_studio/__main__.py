import argparse
import asyncio
import io
import json
import tempfile
import time
from pathlib import Path

from PIL import Image

from .catalog import PRESETS, prompt_for
from .config import MODELS, Settings
from .finance import usage_cost, write_reports
from .media import Media, normalize
from .provider import MockProvider, OpenAIProvider
from .service import Service
from .store import DomainError, Store


async def offline_poc(output: Path):
    with tempfile.TemporaryDirectory(prefix="image-studio-poc-") as temporary:
        root = Path(temporary)
        store, media, provider = Store(root / "db.sqlite3"), Media(root), MockProvider()
        store.consent(101)
        store.grant_demo(101)
        image = io.BytesIO()
        Image.new("RGB", (320, 320), "#e2decf").save(image, "JPEG")
        photo = media.save(101, normalize(image.getvalue()))
        deliveries = []

        async def delivery(user, job, path):
            assert path.is_file()
            deliveries.append(job)

        service = Service(store, media, provider, delivery)
        job = service.submit(101, "offline-hair", "hair", [photo], "short hair")
        assert await service.process(job)
        assert not await service.process(job)
        merge = service.submit(101, "offline-merge", "merge", [photo, photo], "two subjects side by side")
        assert await service.process(merge)
        assert store.wallet(101) == (0, 0)
        try:
            service.submit(101, "no-balance", "hair", [photo], "short hair")
        except DomainError:
            pass
        else:
            raise AssertionError("balance_not_enforced")
        result = {
            "status": "pass",
            "evidence": "offline_mock_workflow_only",
            "provider_calls": provider.calls,
            "results_delivered": len(deliveries),
            "balance": store.wallet(101),
            "external_image_api_calls": 0,
            "visual_quality_verified": False,
            "telegram_network_verified": False,
            "live_payments_verified": False,
        }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


async def live_poc(args, settings):
    if not settings.openai_key:
        raise ValueError("missing_openai_key_set_local_env")
    if args.model not in MODELS or len(args.images) != PRESETS[args.preset].inputs or args.fx <= 0:
        raise ValueError("invalid_poc_parameters")
    prompt = prompt_for(args.preset, args.description)
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    run_id = str(time.time_ns())
    provider = OpenAIProvider(settings.openai_key, args.model, args.quality)
    # Running this command with local inputs explicitly sends them to OpenAI and incurs API fees.
    with tempfile.TemporaryDirectory(prefix="inputs-", dir=output) as temporary:
        images = []
        for i, original in enumerate(args.images):
            path = Path(temporary) / f"{i}.jpg"
            path.write_bytes(normalize(Path(original).read_bytes()))
            images.append(path)
        started = time.monotonic()
        try:
            result = await provider.edit(images, prompt)
        finally:
            await provider.close()
        elapsed = time.monotonic() - started
    target = output / f"{run_id}.jpg"
    target.write_bytes(normalize(result.data))
    cost = usage_cost(result.usage)
    report = {
        "model": args.model,
        "quality": args.quality,
        "size": "1024x1024",
        "preset": args.preset,
        "latency_seconds": elapsed,
        "usage": result.usage,
        "request_id": result.request_id,
        "api_cost_usd": cost,
        "api_cost_rub": cost * args.fx if cost is not None else None,
        "fx_assumption": args.fx,
        "output_file": target.name,
        "identity_score": None,
        "edit_accuracy_score": None,
        "review_pending": True,
    }
    (output / f"{run_id}.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description="Image Studio local MVP")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("run")
    commands.add_parser("billing")
    commands.add_parser("status")
    commands.add_parser("stop")
    finance = commands.add_parser("finance")
    finance.add_argument("--output-dir", default="reports")
    offline = commands.add_parser("offline-poc")
    offline.add_argument("--output", default="reports/offline-poc.json")
    live = commands.add_parser("live-poc", help="Paid one-shot API experiment using consented local photos")
    live.add_argument("--images", nargs="+", required=True)
    live.add_argument("--preset", choices=PRESETS, required=True)
    live.add_argument("--description", required=True)
    live.add_argument("--model", choices=sorted(MODELS), default="gpt-image-2.5-flare-2026-09-08")
    live.add_argument("--quality", choices=["low", "medium", "high", "xhigh", "max"], default="medium")
    live.add_argument("--fx", type=float, default=90)
    live.add_argument("--output-dir", default="data/live-poc")
    release = commands.add_parser("release-job")
    release.add_argument("job")
    refund = commands.add_parser("refund-order")
    refund.add_argument("order")
    pilot = commands.add_parser("pilot-credits", help="Local administrator grants a bounded pilot allowance")
    pilot.add_argument("--user-id", type=int, required=True)
    pilot.add_argument("--amount", type=int, default=3)
    args = parser.parse_args()
    try:
        if args.command == "finance":
            write_reports(Path(args.output_dir))
            print("Finance scenarios and interactive HTML written. Evidence: hypothetical, not measured.")
            return
        if args.command == "offline-poc":
            print(json.dumps(asyncio.run(offline_poc(Path(args.output))), ensure_ascii=False))
            return
        if args.command in {"status", "stop"}:
            from .runtime import RUNTIME_DIR, BotRuntime, status

            current = status()
            if args.command == "stop" and current["running"]:
                BotRuntime(RUNTIME_DIR, "").request_stop()
                current["stop_requested"] = True
            print(json.dumps(current))
            return
        settings = Settings.from_env()
        if args.command == "run":
            from .bot import run

            asyncio.run(run(settings))
        elif args.command == "live-poc":
            print(json.dumps(asyncio.run(live_poc(args, settings)), ensure_ascii=False))
        elif args.command == "billing":
            import uvicorn

            from .billing import create_app

            uvicorn.run(
                create_app(settings, Store(settings.data_dir / "db.sqlite3")),
                host="127.0.0.1",
                port=8088,
                log_level="warning",
                access_log=False,
            )
        elif args.command == "release-job":
            store = Store(settings.data_dir / "db.sqlite3")
            if store.job(args.job)["status"] != "review":
                raise DomainError("release_only_review_jobs")
            store.fail(args.job, "manual_release")
            print("Reserved credits released; external API cost may still have occurred.")
        elif args.command == "pilot-credits":
            if args.user_id <= 0:
                raise ValueError("invalid_user_id")
            store = Store(settings.data_dir / "db.sqlite3")
            store.grant_pilot(args.user_id, args.amount)
            print("Pilot credits granted by local administrator.")
        elif args.command == "refund-order":
            if not settings.billing_enabled:
                raise ValueError("billing_disabled")
            from .billing import YooKassaClient

            async def do_refund():
                gateway = YooKassaClient(settings)
                try:
                    return await gateway.refund(Store(settings.data_dir / "db.sqlite3"), args.order)
                finally:
                    await gateway.close()

            print(json.dumps(asyncio.run(do_refund())))
    except Exception as exc:
        # No traceback or provider payload in ordinary CLI output.
        code = (
            str(exc)
            if isinstance(exc, (ValueError, DomainError)) and str(exc).replace("_", "").isalnum()
            else type(exc).__name__
        )
        parser.exit(1, "Error: " + code + "\n")


if __name__ == "__main__":
    main()
