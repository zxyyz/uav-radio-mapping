# Contributing

The current milestone is a minimal local geometry store. Planned modules are listed in [the roadmap](docs/roadmap.md).

Keep core runtime dependencies small. Hardware adapters and point-cloud tools should be optional. Use explicitly labeled synthetic data in examples and tests. Do not commit local credentials, surveyed coordinates, raw flight logs, commercial map tiles, or point clouds without preparing a publishable dataset.

For behavior changes, run `python3 -m unittest discover -s tests -v`. Keep geometry units and map roles consistent with [the data contract](docs/data-contract.md). Future algorithm contributions should identify the corresponding paper equations and include a deterministic experiment configuration.

Documentation may be Chinese or English. New contributions use the repository's MIT license; existing third-party materials retain their original terms.
