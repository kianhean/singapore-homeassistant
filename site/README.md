# Singapore for Home Assistant — Microsite

Static microsite for **https://ha.kianhean.com/** (zero build step: plain HTML, CSS and vanilla JS).

## Local preview

```sh
python3 -m http.server 8787 --directory site
```

Then open http://localhost:8787/.

## Deploy on Cloudflare Pages (`ha.kianhean.com`)

1. In Cloudflare Dashboard → **Workers & Pages** → **Create** → **Pages** → **Connect to Git**.
2. Select `kianhean/singapore-homeassistant` (`main` branch).
3. Configure build settings:
   - **Framework preset:** None
   - **Build command:** *(leave empty)*
   - **Build output directory:** `site`
4. Under **Custom domains**, add `ha.kianhean.com` (Cloudflare will automatically create the CNAME record on your `kianhean.com` zone).
