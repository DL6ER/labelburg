"""The host names the server answers: those of the home network and those
of HOSTS, as Host and in Origin. That protects against DNS rebinding, where
a website points its own name at the server to reach it from the browser of
a visitor."""
import ipaddress
import logging
from urllib.parse import urlsplit

from werkzeug.sansio.utils import host_is_trusted
from werkzeug.wrappers import Response

logger = logging.getLogger(__name__)

# Host names of the home network, accepted without HOSTS: none of these can
# be registered by others. AVM holds the public zone fritz.box, a FRITZ!Box
# answers its names in the home network.
LOCAL_SUFFIXES = ('.local', '.lan', '.home.arpa', '.home', '.internal', '.localdomain', '.fritz.box')


def host_list(value: str) -> list[str] | None:
    """Host names given comma-separated, None if there are none."""
    return [host.strip() for host in value.split(',') if host.strip()] or None


def _normalized(name: str) -> str:
    """name as names are compared: in lower case, without the trailing dot
    of a fully qualified name."""
    return name.lower().removesuffix('.')


def local_host(host: str) -> bool:
    """Whether host (without port, as _normalized() gives it) is an IP
    address, a single-label name or a name of the home network, e.g.
    labels.local."""
    host = host.strip('[]')
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return '.' not in host or host.endswith(LOCAL_SUFFIXES)


def _hostname(host: str) -> str:
    """host without its port, an IPv6 address with its brackets."""
    if host.startswith('['):
        return host[:host.find(']') + 1]
    return host.partition(':')[0]


def host_allowed(host: str, extra: list[str] | None = None, trusted: list[str] | None = None) -> bool:
    """Whether the server answers requests for host, as Host or in Origin:
    names of the home network and those of HOSTS (extra), with
    TRUSTED_HOSTS set exactly its names."""
    name = _normalized(_hostname(host))
    if not name:
        return False
    if trusted:
        return host_is_trusted(name, [_normalized(n) for n in trusted])
    return local_host(name) or bool(extra and host_is_trusted(name, [_normalized(n) for n in extra]))


def _origin_host(origin: str) -> str:
    return urlsplit(origin).netloc


class HostCheck:
    """WSGI middleware that answers requests only for the allowed host
    names, as Host and in Origin. An opaque Origin ("null", e.g. what
    Android shares with the installed app) names no host to check."""

    def __init__(self, app, wsgi_app):
        self.app, self.wsgi_app = app, wsgi_app

    def __call__(self, environ, start_response):
        config = self.app.config
        trusted = config.get('TRUSTED_HOSTS')
        # MCP_HOSTS is the former name of HOSTS
        extra = [*(config.get('HOSTS') or []), *(config.get('MCP_HOSTS') or [])]
        refused = self._refused(environ, extra, trusted)
        if refused is None:
            # Flask compares TRUSTED_HOSTS once more, case-sensitively: pass
            # the name on in the form it was compared in
            host = environ['HTTP_HOST']
            name = _hostname(host)
            environ['HTTP_HOST'] = _normalized(name) + host[len(name):]
            return self.wsgi_app(environ, start_response)
        logger.info('Refused a request for host %r', refused)
        setting = 'TRUSTED_HOSTS' if trusted else 'HOSTS'
        response = Response(f'This host name is not allowed. Add it to {setting}.', 403, mimetype='text/plain')
        return response(environ, start_response)

    @staticmethod
    def _refused(environ, extra: list[str], trusted: list[str] | None) -> str | None:
        """The first name of the request that is not allowed, None if all are."""
        host, origin = environ.get('HTTP_HOST', ''), environ.get('HTTP_ORIGIN')
        if not host_allowed(host, extra, trusted):
            return host
        # Behind ProxyFix also the Host the connection came with, as a client
        # reaching the server directly can send X-Forwarded-Host itself. The
        # proxy may pass its own name for it, e.g. nginx 127.0.0.1:8013.
        if 'werkzeug.proxy_fix.orig' in environ:
            received = environ['werkzeug.proxy_fix.orig'].get('HTTP_HOST') or ''
            if not host_allowed(received, [*extra, *(trusted or [])]):
                return received
        if origin and origin != 'null' and not host_allowed(_origin_host(origin), extra, trusted):
            return origin
        return None


def install(app):
    # instance/application.py may give them comma-separated like the environment
    for key in ('HOSTS', 'MCP_HOSTS', 'TRUSTED_HOSTS'):
        if isinstance(app.config.get(key), str):
            app.config[key] = host_list(app.config[key])
    app.wsgi_app = HostCheck(app, app.wsgi_app)
