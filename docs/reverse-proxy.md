# Reverse proxy

Behind a reverse proxy, set `PROXY_COUNT=1` and its public name in `HOSTS`, as Labelburg answers only the names of the home network otherwise (see [Host names](../README.md#host-names)):

```yaml
    environment:
      - PROXY_COUNT=1
      - HOSTS=labels.example.com
```

The name the proxy connects to Labelburg with has to be a name of the home network or be listed in `HOSTS` too, e.g., a service name with a dot like `app.docker` or `labelburg.default.svc`: `HOSTS=labels.example.com,labelburg.default.svc`.

The label designer can then also be served below a path, e.g. with nginx:

```nginx
location /labels/ {
    proxy_pass http://127.0.0.1:8013/;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-Host $host;
    proxy_set_header X-Forwarded-Prefix /labels;
}
```

Several instances can be served below different paths of one host. Each keeps its own label in progress, print history and printer in the browser and its printer settings with its labels; the theme and the language of the demo labels are shared.
