from __future__ import annotations

import ctypes
import json
import multiprocessing
import os
import threading
import uuid
from contextlib import contextmanager

import pytest

from genshin_app.agent_chat.credential_generation import (
    MAX_CREDENTIAL_GENERATION,
    CredentialGenerationConflict,
    CredentialGenerationController,
    CredentialGenerationError,
)
from genshin_app.agent_chat.credential_store import (
    _VERSIONED_PREFIX,
    CredentialStoreError,
    WindowsCredentialStore,
    _decode_versioned,
    _encode_versioned,
    _global_mutex_name,
    _mutex_security_sddl,
)


def _assert_public_exception_drops_secret_and_chain(error, *sentinels):
    assert error.__context__ is None
    assert error.__cause__ is None
    controller_frames = []
    traceback = error.__traceback__
    while traceback is not None:
        frame = traceback.tb_frame
        if frame.f_globals.get("__name__") == (
                "genshin_app.agent_chat.credential_generation"):
            controller_frames.append(dict(frame.f_locals))
        traceback = traceback.tb_next
    assert controller_frames
    rendered = repr(controller_frames)
    for sentinel in sentinels:
        assert sentinel not in rendered
        assert all(
            value != sentinel
            for local_values in controller_frames
            for value in local_values.values()
        )


def _assert_codec_exception_drops_value_and_chain(error, *sentinels):
    assert isinstance(error, CredentialStoreError)
    assert error.__context__ is None
    assert error.__cause__ is None
    store_frames = []
    traceback = error.__traceback__
    while traceback is not None:
        frame = traceback.tb_frame
        if frame.f_globals.get("__name__") == "genshin_app.agent_chat.credential_store":
            store_frames.append(dict(frame.f_locals))
        traceback = traceback.tb_next
    assert store_frames
    rendered = repr(store_frames)
    for sentinel in sentinels:
        assert sentinel not in rendered
        assert repr(sentinel)[1:-1] not in rendered
        assert all(
            value != sentinel and value != sentinel.encode("utf-8", errors="surrogatepass")
            for local_values in store_frames
            for value in local_values.values()
        )


def _windows_store_harness(*, raw=None, read_error=None, write_error=None):
    """Exercise WindowsCredentialStore logic without touching Credential Manager."""

    state = {"raw": raw, "writes": []}

    class Harness(WindowsCredentialStore):
        def __init__(self):
            pass

        @contextmanager
        def _mutation_guard(self):
            yield

        def _read_blob(self):
            if read_error is not None:
                raise read_error
            return state["raw"]

        def _write_blob(self, value):
            if write_error is not None:
                raise write_error
            state["raw"] = bytes(value)
            state["writes"].append(bytes(value))

    return Harness(), state


def _hold_windows_credential_mutex(target, acquired, release, details):
    """Child-process helper; it never reads or writes Credential Manager."""

    store = WindowsCredentialStore(target)
    details.put((store._current_user_sid(), store._mutex_name, store._mutex_sddl))
    with store._mutation_guard():
        acquired.set()
        if not release.wait(15):
            raise RuntimeError("test_release_timeout")


class AtomicCredentialDouble:
    available = True

    def __init__(self):
        self._lock = threading.Lock()
        self.generation = 0
        self.secret = None
        self.commits = 0

    def read_versioned(self):
        with self._lock:
            return self.generation, self.secret

    def compare_and_swap(self, *, expected_generation, next_generation, secret):
        with self._lock:
            if self.generation != expected_generation:
                return False
            assert next_generation == expected_generation + 1
            self.generation = next_generation
            self.secret = secret
            self.commits += 1
            return True


def test_first_save_replace_delete_are_generation_bound_and_sanitized():
    secret_one = "SENTINEL_KEY_ONE"
    secret_two = "SENTINEL_KEY_TWO"
    backend = AtomicCredentialDouble()
    first = CredentialGenerationController(backend)
    stale_window = CredentialGenerationController(backend)

    initial = first.status()
    assert initial == {
        "available": True,
        "saved": False,
        "status": "empty",
        "credential_generation": 0,
    }
    saved = first.save(secret_one, expected_generation=0)
    assert saved["credential_generation"] == 1 and saved["saved"] is True
    assert stale_window.status()["credential_generation"] == 1

    with pytest.raises(CredentialGenerationConflict) as stale_save:
        stale_window.save(secret_two, expected_generation=0)
    assert stale_save.value.expected_generation == 0
    assert stale_save.value.actual_generation == 1
    assert first.resolve_for_provider() == (secret_one, 1)

    replaced = stale_window.save(secret_two, expected_generation=1)
    assert replaced["credential_generation"] == 2
    with pytest.raises(CredentialGenerationConflict):
        first.delete(expected_generation=1)
    deleted = first.delete(expected_generation=2)
    assert deleted == {
        "available": True,
        "saved": False,
        "status": "empty",
        "credential_generation": 3,
    }
    with pytest.raises(CredentialGenerationConflict):
        stale_window.save(secret_one, expected_generation=2)
    assert first.resolve_for_provider() == (None, 3)
    assert backend.commits == 3

    public = json.dumps([initial, saved, replaced, deleted], sort_keys=True)
    assert secret_one not in public and secret_two not in public
    assert secret_one not in repr(first) and secret_two not in repr(stale_save.value)


def test_duplicate_generation_has_exactly_one_cross_controller_winner():
    backend = AtomicCredentialDouble()
    controllers = [CredentialGenerationController(backend) for _ in range(8)]
    barrier = threading.Barrier(len(controllers))
    outcomes = []

    def mutate(index):
        barrier.wait()
        try:
            controllers[index].save(f"SENTINEL_{index}", expected_generation=0)
            outcomes.append("success")
        except CredentialGenerationConflict:
            outcomes.append("conflict")

    threads = [threading.Thread(target=mutate, args=(index,))
               for index in range(len(controllers))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=3)
        assert not thread.is_alive()
    assert outcomes.count("success") == 1
    assert outcomes.count("conflict") == len(controllers) - 1
    assert backend.generation == 1 and backend.commits == 1


@pytest.mark.parametrize("generation", [-1, True, "0", None])
def test_invalid_expected_generation_never_calls_backend(generation):
    backend = AtomicCredentialDouble()
    controller = CredentialGenerationController(backend)
    with pytest.raises(CredentialGenerationError):
        controller.save("SENTINEL_VALID", expected_generation=generation)
    assert backend.commits == 0


@pytest.mark.parametrize("secret", ["", " ", "line\nbreak", "x" * 513, None])
def test_invalid_secret_is_rejected_without_persistence(secret):
    backend = AtomicCredentialDouble()
    controller = CredentialGenerationController(backend)
    with pytest.raises(CredentialGenerationError) as caught:
        controller.save(secret, expected_generation=0)
    assert caught.value.code == "credential_value_invalid"
    assert backend.commits == 0


def test_backend_failures_and_status_never_echo_secret():
    sentinel = "SENTINEL_BACKEND_SECRET"

    class FailingBackend(AtomicCredentialDouble):
        def compare_and_swap(self, **_kwargs):
            raise RuntimeError(sentinel)

    controller = CredentialGenerationController(FailingBackend())
    with pytest.raises(CredentialGenerationError) as caught:
        controller.save(sentinel, expected_generation=0)
    assert caught.value.code == "credential_store_write_failed"
    assert sentinel not in str(caught.value) and sentinel not in repr(caught.value)

    class SpoofedControllerErrorBackend(AtomicCredentialDouble):
        def compare_and_swap(self, **_kwargs):
            raise CredentialGenerationError(sentinel)

    spoofed = CredentialGenerationController(SpoofedControllerErrorBackend())
    with pytest.raises(CredentialGenerationError) as spoofed_error:
        spoofed.save(sentinel, expected_generation=0)
    assert spoofed_error.value.code == "credential_store_write_failed"
    assert sentinel not in str(spoofed_error.value)


def test_public_save_failures_drop_backend_chain_and_traceback_key_locals():
    old_secret = "SENTINEL_EXISTING_KEY"
    submitted_secret = "SENTINEL_NEW_KEY_FOR_CONFLICT"
    backend = AtomicCredentialDouble()
    controller = CredentialGenerationController(backend)
    controller.save(old_secret, expected_generation=0)

    with pytest.raises(CredentialGenerationConflict) as conflict:
        controller.save(submitted_secret, expected_generation=0)
    _assert_public_exception_drops_secret_and_chain(
        conflict.value, submitted_secret,
    )

    backend_error = "SENTINEL_BACKEND_EXCEPTION_WITH_KEY"

    class FailingBackend(AtomicCredentialDouble):
        def compare_and_swap(self, **_kwargs):
            raise RuntimeError(backend_error)

    with pytest.raises(CredentialGenerationError) as failed_write:
        CredentialGenerationController(FailingBackend()).save(
            backend_error,
            expected_generation=0,
        )
    assert failed_write.value.code == "credential_store_write_failed"
    _assert_public_exception_drops_secret_and_chain(
        failed_write.value, backend_error,
    )

    invalid_secret = "SENTINEL INVALID KEY"
    with pytest.raises(CredentialGenerationError) as invalid:
        CredentialGenerationController(AtomicCredentialDouble()).save(
            invalid_secret,
            expected_generation=0,
        )
    assert invalid.value.code == "credential_value_invalid"
    _assert_public_exception_drops_secret_and_chain(
        invalid.value, invalid_secret,
    )


def test_read_and_delete_failures_drop_backend_exception_chain():
    backend_error = "SENTINEL_BACKEND_READ_OR_DELETE"

    class FailingReadBackend(AtomicCredentialDouble):
        def read_versioned(self):
            raise RuntimeError(backend_error)

    read_controller = CredentialGenerationController(FailingReadBackend())
    assert read_controller.status() == {
        "available": True,
        "saved": False,
        "status": "error",
        "credential_generation": None,
    }
    with pytest.raises(CredentialGenerationError) as resolve_failure:
        read_controller.resolve_for_provider()
    assert resolve_failure.value.code == "credential_store_read_failed"
    _assert_public_exception_drops_secret_and_chain(
        resolve_failure.value, backend_error,
    )
    with pytest.raises(CredentialGenerationError) as delete_read_failure:
        read_controller.delete(expected_generation=0)
    assert delete_read_failure.value.code == "credential_store_read_failed"
    _assert_public_exception_drops_secret_and_chain(
        delete_read_failure.value, backend_error,
    )

    class FailingDeleteBackend(AtomicCredentialDouble):
        def compare_and_swap(self, **_kwargs):
            raise RuntimeError(backend_error)

    with pytest.raises(CredentialGenerationError) as delete_write_failure:
        CredentialGenerationController(FailingDeleteBackend()).delete(
            expected_generation=0,
        )
    assert delete_write_failure.value.code == "credential_store_write_failed"
    _assert_public_exception_drops_secret_and_chain(
        delete_write_failure.value, backend_error,
    )


def test_only_sanitized_status_is_safe_to_persist(tmp_path):
    sentinel = "SENTINEL_RUNTIME_ONLY_KEY"
    controller = CredentialGenerationController(AtomicCredentialDouble())
    status = controller.save(sentinel, expected_generation=0)
    output = tmp_path / "session-status.json"
    output.write_text(json.dumps(status, ensure_ascii=False), encoding="utf-8")
    persisted = output.read_text(encoding="utf-8")
    assert sentinel not in persisted
    assert "credential_generation" in persisted and '"saved": true' in persisted


def test_unavailable_and_corrupt_backends_fail_closed():
    unavailable = AtomicCredentialDouble()
    unavailable.available = False
    controller = CredentialGenerationController(unavailable)
    assert controller.status() == {
        "available": False, "saved": False, "status": "unavailable",
        "credential_generation": None,
    }
    with pytest.raises(CredentialGenerationError, match="credential_store_unavailable"):
        controller.resolve_for_provider()

    corrupt = AtomicCredentialDouble()
    corrupt.generation = True
    corrupt_controller = CredentialGenerationController(corrupt)
    assert corrupt_controller.status()["status"] == "error"
    with pytest.raises(CredentialGenerationError, match="credential_generation_invalid"):
        corrupt_controller.resolve_for_provider()


def test_generation_is_json_safe_and_backend_must_return_a_real_boolean():
    too_large = AtomicCredentialDouble()
    too_large.generation = MAX_CREDENTIAL_GENERATION + 1
    with pytest.raises(CredentialGenerationError, match="credential_generation_invalid"):
        CredentialGenerationController(too_large).resolve_for_provider()

    exhausted = AtomicCredentialDouble()
    exhausted.generation = MAX_CREDENTIAL_GENERATION
    with pytest.raises(CredentialGenerationError, match="credential_generation_exhausted"):
        CredentialGenerationController(exhausted).delete(
            expected_generation=MAX_CREDENTIAL_GENERATION)

    class NonBooleanBackend(AtomicCredentialDouble):
        def compare_and_swap(self, **_kwargs):
            return 1

    with pytest.raises(CredentialGenerationError, match="credential_store_write_failed"):
        CredentialGenerationController(NonBooleanBackend()).save(
            "SENTINEL_NON_BOOLEAN", expected_generation=0)


def test_windows_blob_codec_preserves_v1_read_and_versioned_tombstone_semantics():
    legacy = b"SENTINEL_LEGACY_KEY"
    assert _decode_versioned(legacy) == (1, legacy.decode())
    printable_prefix_legacy = b"GCA-CREDENTIAL-V1:still-a-legacy-key"
    assert _decode_versioned(printable_prefix_legacy) == (
        1, printable_prefix_legacy.decode())
    assert _decode_versioned(_encode_versioned(4, "SENTINEL_NEW_KEY")) == (
        4, "SENTINEL_NEW_KEY")
    assert _decode_versioned(_encode_versioned(5, None)) == (5, None)


def test_windows_blob_codec_rejects_ambiguous_or_corrupt_envelopes():
    for raw in (
        _VERSIONED_PREFIX + b'{"generation":1,"generation":2,"secret":"x"}',
        _VERSIONED_PREFIX + b'{"generation":1,"secret":"x","extra":true}',
        _VERSIONED_PREFIX + b'{"generation":true,"secret":"x"}',
        _VERSIONED_PREFIX + b'{"generation":1,"secret":"bad key"}',
    ):
        with pytest.raises(CredentialStoreError, match="credential_store_invalid"):
            _decode_versioned(raw)


def test_codec_failures_drop_envelope_and_parser_exception_graph():
    sentinel = "SENTINEL_CODEC_PROVIDER_BODY"
    malformed = _VERSIONED_PREFIX + (
        '{"generation":1,"secret":"' + sentinel + '",'
    ).encode()
    with pytest.raises(CredentialStoreError) as malformed_error:
        _decode_versioned(malformed)
    _assert_codec_exception_drops_value_and_chain(
        malformed_error.value,
        sentinel,
    )

    invalid_secret = "SENTINEL CODEC INVALID SECRET"
    invalid_envelope = _VERSIONED_PREFIX + json.dumps(
        {"generation": 1, "secret": invalid_secret},
        separators=(",", ":"),
    ).encode()
    with pytest.raises(CredentialStoreError) as envelope_error:
        _decode_versioned(invalid_envelope)
    _assert_codec_exception_drops_value_and_chain(
        envelope_error.value,
        invalid_secret,
    )


def test_codec_maps_lone_surrogates_to_sanitized_store_error():
    surrogate = "\ud800"
    encoded_envelope = (
        _VERSIONED_PREFIX + b'{"generation":1,"secret":"\\ud800"}'
    )
    with pytest.raises(CredentialStoreError) as decode_error:
        _decode_versioned(encoded_envelope)
    _assert_codec_exception_drops_value_and_chain(decode_error.value, surrogate)

    with pytest.raises(CredentialStoreError) as encode_error:
        _encode_versioned(1, surrogate)
    _assert_codec_exception_drops_value_and_chain(encode_error.value, surrogate)


def test_windows_store_public_read_drops_raw_and_backend_exception_graph():
    malformed_sentinel = "SENTINEL_WINDOWS_RAW_ENVELOPE"
    malformed = _VERSIONED_PREFIX + (
        '{"generation":1,"secret":"' + malformed_sentinel + '",'
    ).encode()
    store, _state = _windows_store_harness(raw=malformed)
    with pytest.raises(CredentialStoreError) as malformed_error:
        store.read_versioned()
    assert malformed_error.value.code == "credential_store_invalid"
    _assert_codec_exception_drops_value_and_chain(
        malformed_error.value,
        malformed_sentinel,
    )

    backend_sentinel = "SENTINEL_WINDOWS_READ_BACKEND"
    store, _state = _windows_store_harness(
        read_error=RuntimeError(backend_sentinel),
    )
    with pytest.raises(CredentialStoreError) as read_error:
        store.read_versioned()
    assert read_error.value.code == "credential_store_read_failed"
    _assert_codec_exception_drops_value_and_chain(
        read_error.value,
        backend_sentinel,
    )


@pytest.mark.parametrize("operation", ["write", "compare_and_swap", "delete"])
def test_windows_store_public_mutations_drop_secrets_and_backend_exception_graph(
    operation,
):
    old_secret = "SENTINEL_WINDOWS_OLD_KEY"
    new_secret = "SENTINEL_WINDOWS_NEW_KEY"
    backend_sentinel = "SENTINEL_WINDOWS_WRITE_BACKEND"
    store, _state = _windows_store_harness(
        raw=_encode_versioned(4, old_secret),
        write_error=RuntimeError(backend_sentinel),
    )

    with pytest.raises(CredentialStoreError) as caught:
        if operation == "write":
            store.write(new_secret)
        elif operation == "compare_and_swap":
            store.compare_and_swap(
                expected_generation=4,
                next_generation=5,
                secret=new_secret,
            )
        else:
            store.delete()

    assert caught.value.code == "credential_store_write_failed"
    _assert_codec_exception_drops_value_and_chain(
        caught.value,
        old_secret,
        new_secret,
        backend_sentinel,
    )


@pytest.mark.parametrize("operation", ["write", "compare_and_swap", "delete"])
def test_windows_store_public_mutations_drop_read_failure_graph(operation):
    submitted_secret = "SENTINEL_WINDOWS_SUBMITTED_KEY"
    backend_sentinel = "SENTINEL_WINDOWS_MUTATION_READ_BACKEND"
    store, _state = _windows_store_harness(
        read_error=CredentialStoreError(backend_sentinel),
    )

    with pytest.raises(CredentialStoreError) as caught:
        if operation == "write":
            store.write(submitted_secret)
        elif operation == "compare_and_swap":
            store.compare_and_swap(
                expected_generation=0,
                next_generation=1,
                secret=submitted_secret,
            )
        else:
            store.delete()

    assert caught.value.code == "credential_store_read_failed"
    _assert_codec_exception_drops_value_and_chain(
        caught.value,
        submitted_secret,
        backend_sentinel,
    )


@pytest.mark.parametrize("operation", ["write", "compare_and_swap"])
def test_windows_store_public_mutations_drop_invalid_submitted_secret(operation):
    invalid_secret = "SENTINEL WINDOWS INVALID KEY"
    store, _state = _windows_store_harness()

    with pytest.raises(CredentialStoreError) as caught:
        if operation == "write":
            store.write(invalid_secret)
        else:
            store.compare_and_swap(
                expected_generation=0,
                next_generation=1,
                secret=invalid_secret,
            )

    assert caught.value.code == "credential_store_invalid"
    _assert_codec_exception_drops_value_and_chain(
        caught.value,
        invalid_secret,
    )


def test_windows_store_safe_fact_boundary_preserves_generation_and_tombstones():
    legacy_secret = "SENTINEL_WINDOWS_LEGACY_KEY"
    next_secret = "SENTINEL_WINDOWS_REPLACEMENT_KEY"
    store, state = _windows_store_harness(raw=legacy_secret.encode())

    assert store.read_versioned() == (1, legacy_secret)
    store.write(next_secret)
    assert store.read_versioned() == (2, next_secret)
    assert store.compare_and_swap(
        expected_generation=1,
        next_generation=2,
        secret="SENTINEL_STALE_KEY",
    ) is False
    assert store.compare_and_swap(
        expected_generation=2,
        next_generation=3,
        secret=next_secret,
    ) is True
    assert store.delete() is True
    assert store.read_versioned() == (4, None)
    assert store.delete() is False
    assert len(state["writes"]) == 3


def test_global_mutex_identity_is_user_scoped_and_acl_is_not_broad():
    first_sid = "S-1-5-21-100-200-300-1001"
    second_sid = "S-1-5-21-100-200-300-1002"
    first = _global_mutex_name("credential-target", first_sid)
    assert first.startswith("Global\\GenshinCalc.NativeAgent.Credential.")
    assert first == _global_mutex_name("credential-target", first_sid)
    assert first != _global_mutex_name("credential-target", second_sid)
    assert first != _global_mutex_name("other-target", first_sid)

    sddl = _mutex_security_sddl(first_sid)
    assert sddl == (
        f"O:{first_sid}D:P(A;;GA;;;{first_sid})(A;;GA;;;SY)"
    )
    assert all(marker not in sddl for marker in (";;;WD)", ";;;AU)", ";;;IU)"))
    package_sid = "S-1-15-2-100-200-300-400"
    sandboxed = _mutex_security_sddl(first_sid, package_sid)
    assert sandboxed == (
        f"O:{first_sid}D:P(A;;GA;;;{first_sid})"
        f"(A;;GA;;;{package_sid})(A;;GA;;;SY)"
    )
    assert ";;;AC)" not in sandboxed


@pytest.mark.skipif(os.name != "nt", reason="requires Windows named mutexes")
def test_global_mutex_acl_allows_same_user_cross_process_serialization():
    # This validates a real kernel mutex and its current-user DACL without ever
    # calling CredRead/CredWrite/CredDelete. A distinct-login-session smoke is
    # still required on the release Windows matrix.
    target = f"GenshinCalc/TestOnly/{uuid.uuid4()}"
    context = multiprocessing.get_context("spawn")
    acquired = context.Event()
    release = context.Event()
    details = context.Queue()
    child = context.Process(
        target=_hold_windows_credential_mutex,
        args=(target, acquired, release, details),
    )
    child.start()
    try:
        assert acquired.wait(10)
        store = WindowsCredentialStore(target)
        assert store._mutex_name.startswith("Global\\")
        identity = (store._current_user_sid(), store._mutex_name, store._mutex_sddl)
        assert details.get(timeout=5) == identity
        assert store._mutex_sddl == _mutex_security_sddl(
            identity[0], store._current_app_container_sid(),
        )

        with store._mutex_security_attributes() as security:
            handle = store._create_mutex(security, False, store._mutex_name)
        assert handle, ctypes.get_last_error()
        try:
            # WAIT_TIMEOUT proves this process opened the same object and could
            # not enter while the child owned it.
            assert int(store._wait_mutex(handle, 0)) == 0x102
        finally:
            store._close_handle(handle)

        release.set()
        child.join(10)
        assert child.exitcode == 0
        with store._mutation_guard():
            pass
    finally:
        release.set()
        child.join(2)
        if child.is_alive():
            child.terminate()
            child.join(5)


@pytest.mark.skipif(os.name != "nt", reason="requires Windows named mutexes")
def test_global_mutex_creation_failure_is_fail_closed_without_local_fallback():
    secret = "SENTINEL_GLOBAL_MUTEX_FAILURE_KEY"
    store = WindowsCredentialStore(f"GenshinCalc/TestOnly/{uuid.uuid4()}")
    attempted_names = []
    credential_io = []

    def reject_mutex(_security, _initial_owner, name):
        attempted_names.append(name)

    store._create_mutex = reject_mutex
    store._read_blob = lambda: credential_io.append("read")
    store._write_blob = lambda _raw: credential_io.append("write")

    with pytest.raises(CredentialStoreError) as caught:
        store.write(secret)
    assert caught.value.code == "credential_store_lock_failed"
    assert attempted_names == [store._mutex_name]
    assert attempted_names[0].startswith("Global\\")
    assert "Local\\" not in attempted_names[0]
    assert credential_io == []
    _assert_codec_exception_drops_value_and_chain(caught.value, secret)
