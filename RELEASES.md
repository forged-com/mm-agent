# Releases

One entry per tagged release, newest first. Each entry says what changed, shows the change with a worked example, says what you have to do, and gives the digest of the published image so you can pin to it:

```sh
docker run ... ghcr.io/forged-com/mm-agent@sha256:<digest>
```

Pass the same digest in as `FORGD_IMAGE_DIGEST` so it appears in your check-ins and receipts.

## Template

```markdown
## 0.1.1 (2026-11-05)

Image: `ghcr.io/forged-com/mm-agent:0.1.1`
Digest: `sha256:<64 hex characters>`

### What changed

One line per change, written for the person running the container, not for the person who wrote it.

### Worked example

Before and after, using a config or an output line an operator would recognise.

### What you must do

Usually: bump the tag. If a config change is needed, say exactly which key and give the new value.
```

## Example entry

The entry below is illustrative and does not describe a published release.

```markdown
## 0.1.1 (2026-11-05)

Image: `ghcr.io/forged-com/mm-agent:0.1.1`
Digest: `sha256:0000000000000000000000000000000000000000000000000000000000000000`

### What changed

- A bare `mysql://` DSN is now rejected at startup with a message naming the scheme to use, instead of failing on the first job with a missing-driver error.

### Worked example

Before, with `dsn: mysql://readonly@oms-db/oms`, every job failed with:

    mysql engine failed: No module named 'MySQLdb'; install forgd-mm-agent[mysql]

After, the agent exits at startup with:

    source.dsn: scheme 'mysql' needs a driver; use mysql+pymysql:// (installed by forgd-mm-agent[mysql])

### What you must do

If your DSN starts with `mysql://`, change it to `mysql+pymysql://`. Then bump the tag.
```

## Published releases

None yet.
