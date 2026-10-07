import asyncio
import json
import logging

from .catalog import PRESETS, prompt_for
from .media import normalize
from .provider import ProviderError
from .store import DomainError

log = logging.getLogger(__name__)


class Service:
    def __init__(self, store, media, provider, deliver=None, notify=None, *, trial_access=False):
        self.store, self.media, self.provider = store, media, provider
        self.deliver, self.notify = deliver, notify
        self.trial_access = trial_access

    def cost(self, preset):
        choice = PRESETS.get(preset)
        if choice is None:
            raise DomainError("invalid_inputs")
        return 1 if self.trial_access else choice.credits

    def wallet(self, user):
        if self.trial_access and self.store.has_consent(user):
            self.store.grant_trial(user)
        return self.store.wallet(user, trial=self.trial_access)

    def submit(self, user, key, preset, inputs, description):
        choice = PRESETS.get(preset)
        if choice is None or len(inputs) != choice.inputs:
            raise DomainError("invalid_inputs")
        for path in inputs:
            resolved = self.media.path(path)
            if resolved.parent != self.media.path(str(user)) or not resolved.is_file():
                raise DomainError("invalid_inputs")
        prompt = prompt_for(preset, description)
        if self.trial_access:
            self.store.grant_trial(user)
        job = self.store.reserve(user, key, preset, self.cost(preset), trial=self.trial_access)
        payload_name = f"{job}.json"
        payload_path = self.media.path(f"{user}/{payload_name}")
        if not payload_path.exists() and self.store.job(job)["status"] == "queued":
            try:
                self.media.save(user, json.dumps({"inputs": inputs, "prompt": prompt}).encode(), payload_name)
            except OSError:
                self.store.fail(job, "storage_error")
                raise DomainError("storage_error") from None
        return job

    async def process(self, job):
        if not self.store.claim(job):
            return False
        j = self.store.job(job)
        try:
            payload = json.loads(self.media.path(f"{j['user_id']}/{job}.json").read_text(encoding="utf-8"))
            inputs = [self.media.path(path) for path in payload["inputs"]]
            result = await self.provider.edit(inputs, payload["prompt"])
            output = normalize(result.data)
            relative = self.media.save(j["user_id"], output, f"{job}.jpg")
            self.store.finish(job, relative, result.usage, result.request_id)
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
