# Security and privacy boundaries

**Demo-only, loopback-only.** The source is provided for evaluation; this application is not certified for public-facing multi-user deployment.

- No original third-party document text, private 409-chunk Manifest, frozen Gold, private model responses, account tokens or Git history are redistributed.
- The locally generated Bearer token is typed into the browser; it is not built into static JS assets. Do not share it or deploy this local auth mechanism onto the public Internet.
- Untrusted evidence is treated as data, not an instruction or authorization source. Citation authorization originates from serialized evidence IDs.
- Structural citation validation is **not** semantic claim support verification. A syntactically valid citation can still be factually unrelated.
- Rate limiting, deadline and metrics are per process. A production setup needs shared limits, independent log/secret handling, observability, incident response and ingress protection.
- No current full vulnerability scan or dependency-license legal sign-off is claimed. Check `THIRD-PARTY-LICENSE-REVIEW.md` and the inventory before any external release.

Report security concerns privately to the repository owner via the platform's contact route rather than filing credentials in a public issue. No guaranteed disclosure SLA is promised.
