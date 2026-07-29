## What and why

<!-- What changed, and what problem it solves. Link the issue if there is one. -->

## Cost impact

<!--
Required. No significant fixed-cost AWS resource may be introduced silently.

Pick one:
- No AWS resources created or changed.
- Creates <resource>: ~$X/month. Cost table in the README updated.
- Removes <resource>: saves ~$X/month.

Quote a price with its source and date. `scripts/aws-prices.py` reproduces the
figures in the README's cost table.
-->

## Scale-to-zero and idempotency

<!--
Only if this touches ingestion, scaling or the GPU path.
- Does an idle system still have zero GPU pods and zero GPU nodes?
- Is processing still idempotent under duplicate delivery?
- Does search still work with no GPU present?
-->

## Tests run

<!-- The exact commands and their results. Not "tests pass". -->

```bash
make check
make up && make smoke
```

## Documentation

- [ ] Reasoning recorded next to the code, if a meaningful tradeoff was decided
- [ ] Metrics added, if a new asynchronous stage exists
- [ ] Teardown path exists for any new cloud component
- [ ] README updated if the architecture moved

## Remaining risks

<!-- What could still be wrong. "None" is rarely true. -->
