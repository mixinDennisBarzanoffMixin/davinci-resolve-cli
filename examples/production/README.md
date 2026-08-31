# Production pipeline examples

`kia-k8-research.json` is an evidence-labeled research artifact for the sample
CarsBG11 listing. It demonstrates the contract expected by the
`dvr production plan` command; it is not a claim that model-family equipment
exists on this exact car.
`kia-k8-corrections.json` demonstrates timestamp-preserving, reviewed lexical
corrections; it must still be checked against the recording before publication.

```bash
cp examples/production/kia-k8-research.json /path/to/kia-k8/research.json
dvr production plan --project-dir /path/to/kia-k8
```

Listing prices and availability change. A real run should regenerate research
and preserve `retrieved_at` rather than treating this fixture as live inventory.
