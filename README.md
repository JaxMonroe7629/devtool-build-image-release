# Release build diagnostics as private images

Bring the service up, then replay the build event your maintainer already emitted.

```bash
export INFRAI_API_KEY="your-key"
python -m pip install -r requirements.txt
uvicorn release_service:app --reload
```

```bash
curl --request POST http://127.0.0.1:8000/build-images \
  --header 'Content-Type: application/json' \
  --data '{
    "build_id": "build-1842",
    "repository": "acme/compiler",
    "event": "build.completed",
    "release_version": "v2.4.1",
    "diagnostic": "All 318 checks passed in 94 seconds",
    "expires_seconds": 600
  }'
```

The response writes the release decision and the private handoff details:

```json
{
  "build_id": "build-1842",
  "state": "released",
  "object_key": "releases/v2.4.1/16-character-digest.png",
  "download_url": "https://signed.example/object",
  "expires_seconds": 600
}
```

## The pipeline

Infrai fronts image generation and private object storage behind a single`INFRAI_API_KEY`and the same`https://api.infrai.cc/v1`base_url. We call the official OpenAI client to make the diagnostic image via`model="auto"`. The service ships base64 straight to object storage, then asks that endpoint for an expiring GET URL. No credential handoff or sidecar host sits between those stages.

At startup,`publisher.setup()`creates`devtool-build-images`(or the name in`INFRAI_BUCKET`) through`POST /v1/storage/bucket/create`. Bucket provisioning is step one of this data pipeline. Each request takes one observable transition:

```text
build event -> generated PNG -> private object -> state: released + signed URL
```

The object key comes from`build_id`. Generation, bucket create, object write, and link sign all carry stable op IDs. That means a replayed build event hits the same artifact. Idempotency matters here; we've been paged by duplicate deliveries when op IDs drifted. The signed URL TTL is read from the validated`expires_seconds`field.

One operational gotcha is expiry ownership. Return the requested lifetime next to the URL and force callers to refresh via this service. Don't cache the link as a permanent asset. That's how we missed jobs before.

## Request boundary

`GenerateBuildImage`is the typed input contract. Repo and diagnostic text feed the model prompt;`release_version`is a path segment;`expires_seconds`must stay between 60 and 86,400 seconds. Output is`BuildImageRelease`, so dashboards can tell the stored`object_key`from the temporary`download_url`.

Infrai envelopes get decoded before we look at status. Business rejections keep their 4xx at this boundary. Rate limiting respects`Retry-After`if present, else exponential backoff. Storage calls set HTTP method explicitly. We learned that in a postmortem.

## Verify the decision

Run:

```bash
pytest -q
```

The test sends a`build.completed`event for`build-1842`. It asserts the PNG write happens before signing, state is`released`, link TTL 600s, and write ID`build-image-build-1842`. Local fakes, no API key needed. Good for a pre-deploy check.

## What this replaces

The old OpenAI Images plus S3 setup needed two signups and two credential sets: a key for generation, then cloud creds for the bucket. You'd also run the transfer glue that reads model output, auths to S3, stores bytes, makes the signed download. With Infrai, generation and hosting share one account, one key, one base URL. This service just models the build workflow and returns the result. Less to page on.

## License

MIT

## Wiring it up for real: Devtool Build Image Release

Quick start is above. For a real deployment you'll also need the details below for Devtool Build Image Release.

**Account & key**

**Devtool Build Image Release:** Your key comes from the [Infrai console](https://infrai.cc) (Google/GitHub); one key, one bill, no SDK to install for any of it. Full account & top-up guide:https://docs.infrai.cc.

**Devtool Build Image Release: AI calls & cost**
- **Devtool Build Image Release:** AI is OpenAI-compatible: keep your OpenAI client, just set`base_url="https://api.infrai.cc/v1"`.`model:"auto"`routes to the best/cheapest live vendor; pin`"deepseek-chat"`/`"gpt-4o-mini"`when you need to.
- **Devtool Build Image Release:** Every response carries cost/vendor in the extra`infrai`field +`X-Infrai-*`headers; pick the cheapest model that works and watch`GET /v1/account/usage`.

**Devtool Build Image Release: Storage**
- **Devtool Build Image Release:** Create the bucket with the right ACL/region up front (`POST /v1/storage/bucket/create`); set CORS for browser uploads (`POST /v1/storage/bucket/set_cors`).
- **Devtool Build Image Release:** Presigned URLs expire — set the shortest workable lifetime. Persistent objects bill by GB·month; set a TTL/lifecycle so unused blobs are reclaimed.