"""Resource loading, and the workspace sandbox in particular.

The three escape vectors of docs/03-yaml-and-io.md section 5 each get a test:
lexical traversal, a symlink at the final path component, and a symlink at an
intermediate component. A fourth test covers non-regular files.
"""

from __future__ import annotations

import errno
import gc
import os

import pytest

from fastraml.loaders import (
    FileLoader,
    HTTPLoader,
    LoaderError,
    SafeFileLoader,
    SchemeLoader,
    UnsupportedSchemeError,
    WorkspaceEscapeError,
    build_loader,
)
from fastraml.uris import path_to_file_uri

WINDOWS = os.name == 'nt'


@pytest.fixture
def workspace(tmp_path):
    """A workspace root with one file inside it and one secret outside.

    Written as bytes, not text: `write_text` translates newlines on Windows,
    and a loader test must see exactly the bytes on disk.
    """
    root = tmp_path / 'workspace'
    (root / 'types').mkdir(parents=True)
    (root / 'api.raml').write_bytes(b'#%RAML 1.0\ntitle: X\n')
    (root / 'types' / 'user.raml').write_bytes(b'#%RAML 1.0 DataType\ntype: string\n')
    (tmp_path / 'secret.txt').write_bytes(b'do not read me')
    return root


def _symlink(target, link) -> bool:
    """Create a symlink, reporting whether the platform allowed it.

    Windows needs Developer Mode or elevation, so these tests skip rather than
    fail on a stock machine.
    """
    try:
        os.symlink(target, link)
    except (OSError, NotImplementedError):
        return False
    return True


class TestSafeFileLoader:
    def test_reads_a_file_inside_the_root(self, workspace):
        loader = SafeFileLoader(workspace)
        data = loader.load(path_to_file_uri(workspace / 'types' / 'user.raml'))
        assert data.startswith(b'#%RAML 1.0 DataType')

    def test_refuses_lexical_traversal(self, workspace):
        loader = SafeFileLoader(workspace)
        escaping = path_to_file_uri(workspace / '..' / 'secret.txt')
        with pytest.raises(WorkspaceEscapeError):
            loader.load(escaping)

    def test_the_refusal_carries_the_root_that_would_have_worked(self, workspace):
        """The nearest directory holding both, so a caller can name it.

        The message cannot: which flag widens the root is the CLI's vocabulary.
        """
        loader = SafeFileLoader(workspace)
        with pytest.raises(WorkspaceEscapeError) as caught:
            loader.load(path_to_file_uri(workspace / '..' / 'secret.txt'))
        assert caught.value.info['suggested_root'] == str(workspace.parent)
        assert caught.value.info['root'] == str(workspace)

    def test_no_root_is_suggested_when_widening_would_reach_the_filesystem_root(self, workspace, monkeypatch):
        """Widening to `/` hands over every file the process can reach.

        That is the sandbox this refusal exists to keep, so there is no
        suggestion to make and the caller prints no hint.
        """
        monkeypatch.setattr(os.path, 'commonpath', lambda _paths: os.path.abspath(os.sep))
        loader = SafeFileLoader(workspace)
        with pytest.raises(WorkspaceEscapeError) as caught:
            loader.load(path_to_file_uri(workspace / '..' / 'secret.txt'))
        assert caught.value.info['suggested_root'] == ''

    def test_the_refusal_does_not_repr_its_paths(self, workspace):
        """`!r` on a Windows path doubles every separator.

        The one thing a reader wants to copy came out unusable, so the paths are
        `info` values and the message is the invariant sentence (docs/11 § 6).
        """
        loader = SafeFileLoader(workspace)
        with pytest.raises(WorkspaceEscapeError) as caught:
            loader.load(path_to_file_uri(workspace / '..' / 'secret.txt'))
        assert str(caught.value) == 'path is outside the workspace root'
        assert '\\\\' not in caught.value.info['path']

    def test_refuses_a_symlink_at_the_final_component(self, workspace, tmp_path):
        link = workspace / 'leak.raml'
        if not _symlink(tmp_path / 'secret.txt', link):
            pytest.skip('platform does not permit symlink creation')
        loader = SafeFileLoader(workspace)
        with pytest.raises(WorkspaceEscapeError):
            loader.load(path_to_file_uri(link))

    def test_the_refusal_is_reported_as_an_escape_and_not_as_an_io_error(self, workspace, monkeypatch):
        """`O_NOFOLLOW` refusing the final component is `ELOOP`, and says so.

        The symlink test above cannot reach this on a platform with no
        `O_NOFOLLOW` or no symlink privilege, so it only ever ran on one of the
        two CI jobs — and the errno it turns on was read from the wrong module,
        which a `getattr` default turned into a comparison against `None` that
        no errno equals. The refusal still held; the diagnostic said the file
        could not be read rather than that a symlink had been planted.

        Raised rather than symlinked, so the mapping is pinned on every
        platform.
        """
        loader = SafeFileLoader(workspace)

        def refuse(*_args, **_kwargs):
            raise OSError(errno.ELOOP, 'Too many levels of symbolic links')

        monkeypatch.setattr(os, 'open', refuse)
        with pytest.raises(WorkspaceEscapeError, match='refusing to follow symlink'):
            loader.load(path_to_file_uri(workspace / 'api.raml'))

    def test_refuses_a_symlink_at_an_intermediate_component(self, workspace, tmp_path):
        # O_NOFOLLOW only guards the last component, so this is the case the
        # realpath re-check exists for.
        outside = tmp_path / 'outside'
        outside.mkdir()
        (outside / 'secret.raml').write_bytes(b'leaked')
        if not _symlink(outside, workspace / 'linked'):
            pytest.skip('platform does not permit symlink creation')
        loader = SafeFileLoader(workspace)
        with pytest.raises(WorkspaceEscapeError):
            loader.load(path_to_file_uri(workspace / 'linked' / 'secret.raml'))

    @pytest.mark.skipif(WINDOWS, reason='no os.mkfifo on Windows')
    def test_refuses_a_non_regular_file(self, workspace):
        fifo = workspace / 'pipe'
        os.mkfifo(fifo)
        loader = SafeFileLoader(workspace)
        # Opening a FIFO for reading blocks until a writer appears; the
        # regular-file check must reject it before any read happens.
        with pytest.raises(LoaderError, match='not a regular file'):
            loader.load(path_to_file_uri(fifo))

    def test_missing_file_reports_the_path(self, workspace):
        loader = SafeFileLoader(workspace)
        with pytest.raises(LoaderError):
            loader.load(path_to_file_uri(workspace / 'nope.raml'))

    def test_root_itself_may_be_a_symlink(self, tmp_path):
        # A workspace reached through a symlink is legitimate; containment is
        # compared resolved-to-resolved so this must not be a false positive.
        real = tmp_path / 'real'
        real.mkdir()
        (real / 'api.raml').write_bytes(b'ok')
        if not _symlink(real, tmp_path / 'link'):
            pytest.skip('platform does not permit symlink creation')
        loader = SafeFileLoader(tmp_path / 'link')
        assert loader.load(path_to_file_uri(tmp_path / 'link' / 'api.raml')) == b'ok'

    def test_max_bytes_reads_one_byte_past_the_limit(self, workspace):
        # The caller detects an oversized include by the extra byte, without the
        # loader having read the whole file.
        big = workspace / 'big.raml'
        big.write_bytes(b'x' * 5000)
        loader = SafeFileLoader(workspace)
        assert len(loader.load(path_to_file_uri(big), max_bytes=100)) == 101

    def test_max_bytes_returns_a_short_file_whole(self, workspace):
        loader = SafeFileLoader(workspace)
        data = loader.load(path_to_file_uri(workspace / 'api.raml'), max_bytes=10_000)
        assert data.endswith(b'title: X\n')


class TestFileLoader:
    def test_reads_without_a_sandbox(self, workspace, tmp_path):
        # Documented behaviour: no containment check at all.
        assert FileLoader().load(path_to_file_uri(tmp_path / 'secret.txt')) == b'do not read me'

    def test_wraps_os_errors(self, tmp_path):
        with pytest.raises(LoaderError):
            FileLoader().load(path_to_file_uri(tmp_path / 'absent'))


class _FakeResponse:
    def __init__(self, status_code, content):
        self.status_code = status_code
        self.content = content


class _FakeClient:
    """Minimal stand-in for httpx.Client / requests.Session."""

    def __init__(self, responses):
        self.responses = responses
        self.calls: list[str] = []

    def get(self, url):
        self.calls.append(url)
        try:
            return self.responses[url]
        except KeyError:
            return _FakeResponse(404, b'')


class TestHTTPLoader:
    def test_reads_a_successful_response(self):
        client = _FakeClient({'https://e.com/t.raml': _FakeResponse(200, b'#%RAML 1.0 Trait\n')})
        assert HTTPLoader(client).load('https://e.com/t.raml') == b'#%RAML 1.0 Trait\n'

    def test_rejects_a_non_2xx_status(self):
        client = _FakeClient({})
        with pytest.raises(LoaderError, match='status 404'):
            HTTPLoader(client).load('https://e.com/missing.raml')

    def test_wraps_a_transport_failure(self):
        class Broken:
            def get(self, url):
                msg = 'connection refused'
                raise RuntimeError(msg)

        with pytest.raises(LoaderError, match='connection refused'):
            HTTPLoader(Broken()).load('https://e.com/t.raml')

    def test_honours_max_bytes(self):
        client = _FakeClient({'https://e.com/t.raml': _FakeResponse(200, b'x' * 5000)})
        assert len(HTTPLoader(client).load('https://e.com/t.raml', max_bytes=100)) == 101


class TestAnAsyncClientIsRefused:
    """A parse is one synchronous descent, so there is nowhere to await.

    Unrefused, an `httpx.AsyncClient` fails two lines later as `'coroutine'
    object has no attribute 'status_code'`, with an un-awaited coroutine warning
    behind it — neither of which names the mistake or the fix.
    """

    def test_at_construction_where_the_client_says_what_it_is(self):
        class AsyncClient:
            async def get(self, url): ...

        with pytest.raises(LoaderError, match='asynchronous'):
            HTTPLoader(AsyncClient())

    def test_at_the_call_for_a_wrapper_that_only_returns_an_awaitable(self):
        # `get` is an ordinary function, so `iscoroutinefunction` says nothing;
        # what it returns is what gives it away.
        class Wrapper:
            def get(self, url):
                async def fetch(): ...

                return fetch()

        with pytest.raises(LoaderError, match='asynchronous'):
            HTTPLoader(Wrapper()).load('https://e.com/t.raml')

    def test_the_refusal_carries_no_un_awaited_coroutine_warning(self, recwarn):
        class Wrapper:
            def get(self, url):
                async def fetch(): ...

                return fetch()

        with pytest.raises(LoaderError):
            HTTPLoader(Wrapper()).load('https://e.com/t.raml')
        gc.collect()  # the warning is emitted when the coroutine is collected
        assert [w for w in recwarn if 'never awaited' in str(w.message)] == []

    def test_a_synchronous_client_is_untouched(self):
        client = _FakeClient({'https://e.com/t.raml': _FakeResponse(200, b'ok')})
        assert HTTPLoader(client).load('https://e.com/t.raml') == b'ok'


class TestSchemeLoader:
    def test_dispatches_by_scheme(self, workspace):
        client = _FakeClient({'https://e.com/t.raml': _FakeResponse(200, b'remote')})
        loader = SchemeLoader({'file': SafeFileLoader(workspace), 'https': HTTPLoader(client)})
        assert loader.load(path_to_file_uri(workspace / 'api.raml')).startswith(b'#%RAML')
        assert loader.load('https://e.com/t.raml') == b'remote'

    def test_rejects_an_unregistered_scheme(self, workspace):
        loader = SchemeLoader({'file': SafeFileLoader(workspace)})
        with pytest.raises(UnsupportedSchemeError, match='https'):
            loader.load('https://e.com/t.raml')


class TestBuildLoader:
    def test_remote_loading_is_off_by_default(self, workspace):
        loader = build_loader(workspace)
        assert set(loader.loaders) == {'file'}
        with pytest.raises(UnsupportedSchemeError):
            loader.load('https://e.com/t.raml')

    def test_an_http_client_enables_both_remote_schemes(self, workspace):
        loader = build_loader(workspace, http_client=_FakeClient({}))
        assert set(loader.loaders) == {'file', 'http', 'https'}

    def test_a_custom_file_loader_replaces_the_sandbox(self, workspace):
        loader = build_loader(workspace, file_loader=FileLoader())
        assert isinstance(loader.loaders['file'], FileLoader)
