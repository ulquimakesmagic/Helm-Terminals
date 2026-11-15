# Public visibility review — 15 November 2026

The user explicitly requested making Helm-Terminals public to restore CI capacity. Before changing visibility, all available remote branches/tags were fetched and Gitleaks 8.30.1 scanned 102 commits (approximately 4.86 MB). Its executable was downloaded from the official release and verified against the published SHA-256 checksum.

Eight findings were reviewed individually. Four identify public TRON contract addresses in offline tests; two identify public Solana mint addresses in visual-QA responses; one is a disposable local SQLite/ASGI session from the documented fixture generator; one matches an empty API-key assignment followed by a numeric fee setting. No live credential was identified among those findings. Exact historical fingerprints are documented in `.gitleaksignore`; no entire directory or generic credential rule is excluded.

Repository issue/PR bodies and comments were also exported locally and scanned: approximately 75.86 KB, with no detected secrets. Exports, raw scanner reports, credentials and local runtime files remain outside tracked source. The review does not reveal deployment variables or encrypted GitHub secrets.

Repository visibility was changed to PUBLIC and verified through GitHub. Rerunning the previously billing-blocked workflow now starts jobs successfully; contract, frontend and dependency checks passed on that earlier commit. Matching release checks for the new pricing commit still need to complete. A secret scan is a bounded check, not a guarantee that every possible sensitive value can be recognized.
