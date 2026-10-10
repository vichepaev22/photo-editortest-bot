import asyncio
import json
import logging
import time

from .catalog import PRESETS, prompt_for
from .document_photo import DOCUMENT_PRESETS, prepare_document_sheet
from .media import MAX_BYTES, normalize
from .provider import ProviderError
from .store import DomainError

log = logging.getLogger(__name__)
RECENT_INPUTS = "recent-inputs.json"
INPUT_TTL = 86400
INPUT_RECORD_LIMIT = 16384


class Service:
    def __init__(
        self, store, media, provider, deliver=None, notify=None, *, trial_access=False, unlimited_user_id=0,
    ):
        self.store, self.media, self.provider = store, media, provider
        self.deliver, self.notify = deliver, notify
        self.trial_access = trial_access
        self.unlimited_user_id = unlimited_user_id

    def is_unlimited(self, user):
        return (
            type(self.unlimited_user_id) is int
            and type(user) is int
            and 0 < user < 2**52
            and user == self.unlimited_user_id
        )

    def cost(self, preset):
        choice = PRESETS.get(preset)
        if choice is None:
            raise DomainError("invalid_inputs")
        return 1 if self.trial_access else choice.credits

    def wallet(self, user):
        use_trial = self.trial_access and not self.store.has_manual_access(user)
        if use_trial and not self.is_unlimited(user) and self.store.has_consent(user):
            self.store.grant_trial(user)
        return self.store.wallet(user, trial=use_trial)

    def _owned_recent_file(self, user, relative, limit):
        if not isinstance(relative, str) or len(relative) > 1024:
            return None
        parts = relative.replace("\\", "/").split("/")
        if len(parts) != 2 or parts[0] != str(user) or parts[1] in {"", ".", ".."}:
            return None
        try:
            path = self.media.path(relative)
            # Compare against the literal owner directory: symlinks cannot transfer ownership.
            if path.parent != self.media.root / str(user) or not path.is_file():
                return None
            stat = path.stat()
            if not 0 < stat.st_size <= limit or time.time() - stat.st_mtime > INPUT_TTL:
                return None
            return path
        except (ValueError, OSError):
            return None

    def _originals(self, user, inputs, jobs):
        if not isinstance(inputs, list) or len(inputs) not in {1, 2}:
            return []
        paths = []
        outputs = {f"{job['id']}.jpg" for job in jobs}
        for job in jobs:
            if isinstance(job.get("result"), str):
                try:
                    outputs.add(self.media.path(job["result"]).name)
                except ValueError:
                    pass
        for relative in inputs:
            path = self._owned_recent_file(user, relative, MAX_BYTES)
            if (path is None or path.name in outputs or path in paths
                    or path.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp"}):
                return []
            try:
                with path.open("rb") as file:
                    normalize(file.read(MAX_BYTES + 1))
            except (ValueError, OSError):
                return []
            paths.append(path)
        return [str(path.relative_to(self.media.root)) for path in paths]

    def _input_record(self, user, relative):
        path = self._owned_recent_file(user, relative, INPUT_RECORD_LIMIT)
        if path is None:
            return None
        try:
            with path.open("rb") as file:
                data = file.read(INPUT_RECORD_LIMIT + 1)
            if len(data) > INPUT_RECORD_LIMIT:
                return None
            record = json.loads(data)
            return record if isinstance(record, dict) else None
        except (ValueError, OSError, UnicodeError, RecursionError):
            return None

    def remember_inputs(self, user, inputs):
        if type(user) is not int or user < 0:
            raise DomainError("invalid_inputs")
        if not self.store.has_consent(user):
            raise DomainError("consent_required")
        checked = self._originals(user, inputs, self.store.jobs(user))
        if not checked:
            raise DomainError("invalid_inputs")
        try:
            self.media.save(user, json.dumps({"inputs": checked}).encode("utf-8"), RECENT_INPUTS)
        except OSError:
            raise DomainError("storage_error") from None

    def reusable_inputs(self, user, count):
        if type(user) is not int or user < 0 or type(count) is not int or count not in {1, 2}:
            return []
        if not self.store.has_consent(user):
            return []
        jobs = self.store.jobs(user)
        relative = f"{user}/{RECENT_INPUTS}"
        try:
            recent = self.media.path(relative)
            if recent.parent != self.media.root / str(user):
                return []
            present = recent.exists()
        except (ValueError, OSError):
            return []
        if present:
            record = self._input_record(user, relative)
        elif jobs:
            # Legacy users have no upload metadata. Use only the latest owned job's
            # private payload, never an arbitrary JSON file or its generated result.
            latest = max(jobs, key=lambda job: job["created"])
            record = self._input_record(user, f"{user}/{latest['id']}.json")
        else:
            return []
        if record is None:
            return []
        checked = self._originals(user, record.get("inputs"), jobs)
        return checked if len(checked) == count else []

    def submit(self, user, key, preset, inputs, description):
        choice = PRESETS.get(preset)
        if choice is None or len(inputs) != choice.inputs:
            raise DomainError("invalid_inputs")
        for path in inputs:
            # Selection may precede expiry; revalidate before reserving a new use.
            if self._owned_recent_file(user, path, MAX_BYTES) is None:
                raise DomainError("invalid_inputs")
        prompt = prompt_for(preset, description)
        quota_exempt = self.is_unlimited(user)
        use_trial = self.trial_access
        if self.trial_access and self.store.has_manual_access(user):
            previous = self.store.job_for_request(user, key)
            use_trial = bool(previous["trial"]) if previous else False
        for attempt in range(3):
            try:
                job = self.store.reserve(
                    user, key, preset, self.cost(preset), trial=use_trial, quota_exempt=quota_exempt,
                )
                break
            except DomainError as error:
                if attempt == 2 or not use_trial or quota_exempt:
                    raise
                if str(error) == "manual_access_required":
                    use_trial = False
                elif str(error) == "trial_not_granted":
                    # Existing requests return before this check. A replay cannot
                    # grant a new trial, and conversion cannot allocate it twice.
                    self.store.grant_trial(user)
                    use_trial = not self.store.has_manual_access(user)
                else:
                    raise
        payload_name = f"{job}.json"
        payload_path = self.media.path(f"{user}/{payload_name}")
        if not payload_path.exists() and self.store.job(job)["status"] == "queued":
            try:
                self.media.save(user, json.dumps({"inputs": inputs, "prompt": prompt}).encode(), payload_name)
            except OSError:
                self.store.fail(job, "storage_error")
                raise DomainError("storage_error") from None
            try:
                self.store.admin_capture_job(job, user, description)
            except Exception:
                # Optional owner monitoring cannot prevent the reserved user job.
                log.warning("admin_job_capture_failed")
        return job

    async def process(self, job):
        if not self.store.claim(job):
            return False
        j = self.store.job(job)
        try:
            payload = json.loads(self.media.path(f"{j['user_id']}/{job}.json").read_text(encoding="utf-8"))
            inputs = [self.media.path(path) for path in payload["inputs"]]
            if j["preset"] == "document_original":
                with inputs[0].open("rb") as source:
                    data = source.read(MAX_BYTES + 1)
                usage, request_id = {}, None
            else:
                result = await self.provider.edit(inputs, payload["prompt"])
                data, usage, request_id = result.data, result.usage, result.request_id
            if j["preset"] in DOCUMENT_PRESETS:
                output = prepare_document_sheet(data)
                extension = "png"
            else:
                output = normalize(data)
                extension = "jpg"
            relative = self.media.save(j["user_id"], output, f"{job}.{extension}")
            self.store.finish(job, relative, usage, request_id)
        except asyncio.CancelledError:
            # Running remains reserved. Recovery marks review; no second paid call.
            raise
        except (ProviderError, ValueError, OSError, KeyError) as exc:
            code = str(exc) if isinstance(exc, ProviderError) else "processing_error"
            self.store.fail(job, code)
            if self.notify:
                await self._notify_safely(j["user_id"], job)
            return False
        if self.deliver:
            try:
                await self.deliver(j["user_id"], job, self.media.path(relative))
                self.store.delivered(job)
            except Exception:
                log.warning("delivery_failed job=%s", job)
        return True

    async def _notify_safely(self, user, job):
        try:
            await self.notify(user, job)
        except Exception:
            log.warning("notification_failed job=%s", job)

    async def worker(self):
        self.store.recover()
        while True:
            try:
                for job in self.store.jobs():
                    if job["status"] == "queued":
                        await self.process(job["id"])
                protected = [
                    j["user_id"] for j in self.store.jobs() if j["status"] in {"queued", "running", "review"}
                ]
                self.media.cleanup(protected)
            except asyncio.CancelledError:
                raise
            except Exception:
                # Do not print exception payloads, prompts or credentials.
                log.error("worker_error; interrupted tasks require support")
                self.store.recover()
            await asyncio.sleep(1)

    def delete(self, user):
        self.store.revoke_consent(user)
        self.media.delete_user(user)
