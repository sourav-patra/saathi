# Locks

Each row is a document the owner may lock. Recompute a hash with `sha256sum <path>` and compare it before you rely on a document. A changed file needs a new review and a new row; an old row never covers new bytes (POLICY PL-24). The File column is the exact file name. The four reviews A to D, their finding ids and the resolution of each are in `docs/evidence/reviews/RECEIPT-design-reviews-2026-11-09.md`.

| File | Revision | sha256 of the file | Date | Reviewer | Result | Where |
|---|---|---|---|---|---|---|
| `PRD-saathi-2026-11-09.md` | first draft | `a784aba1ef15c35c41f01a04e0d94248a008f416c9307f2462190187f27e4b29` | 2026-11-09 | A (adversarial), B (compliance) | reviewed, changes requested (R) | private archive, not in this repository |
| `SPEC-saathi-assurance-2026-11-09.md` | first draft | `78c70b97ae17979a73ea3c484a2fc8e7a6fe0410d93ee15c297227c8b63517eb` | 2026-11-09 | A (adversarial), B (compliance) | reviewed, changes requested (R) | private archive, not in this repository |
| `POLICY-saathi-2026-11-09.md` | first draft | `45e5b4b934ec2474a04fc94bd913b3f63b5b7a017785503f90c49f2b1d5688ea` | 2026-11-09 | A (adversarial), B (compliance) | reviewed, changes requested (R) | private archive, not in this repository |
| `PRD-saathi-2026-11-09-r1.md` | r1 | `021742ba14e4d154f291d059aaf1add458e2e3b90c36992990d7518ffae1434a` | 2026-11-09 | C (adversarial), D (compliance) | reviewed, changes requested (R) | private archive, not in this repository |
| `SPEC-saathi-safety-2026-11-09.md` | r1 | `ae3b61c97130022e8ce1b957bdc3e0d0fe73842816fd427cf91976b936e91dd4` | 2026-11-09 | C (adversarial), D (compliance) | reviewed, changes requested (R) | private archive, not in this repository |
| `SPEC-saathi-assurance-2026-11-09-r1.md` | r1 | `cd948b6b8c4a112f66350d4bddd3483a08980b97c7ab9737e2913b70a95e548a` | 2026-11-09 | C (adversarial), D (compliance) | reviewed, changes requested (R) | private archive, not in this repository |
| `SPEC-saathi-evidence-2026-11-09.md` | r1 | `955dd4d2129090bdc3114a819282f26c777e815a54ff8cdfc797663143e6a4c1` | 2026-11-09 | C (adversarial), D (compliance) | reviewed, changes requested (R) | private archive, not in this repository |
| `POLICY-saathi-2026-11-09-r1.md` | r1 | `72906ab9911d42af0645f9c72a9f01b2998209ab3184cf333ac47310fe26e35b` | 2026-11-09 | C (adversarial), D (compliance) | reviewed, changes requested (R) | private archive, not in this repository |
| `RECORD-saathi-decisions-2026-11-09.md` | r1 | `a47572d1ecb3f55d97af1a6fa99c1e4c793906284ee9d78da6ccd7a2485e6c51` | 2026-11-09 | C (adversarial), D (compliance) | reviewed, changes requested (R) | private archive, not in this repository |
| `PRD-saathi-2026-11-09-r2.md` | r2 | `42304c21717b9f1cac205bf447aebbadfeb930e30e4a15aca818db4359380a20` | 2026-11-09 | none yet | pending review | this repository, `docs/design/` |
| `SPEC-saathi-requests-2026-11-09.md` | r2 set, new | `12421a29fa138591e28f57c643dd685c3cbe161eef3dd0b51e14faa2616fd588` | 2026-11-09 | none yet | pending review | this repository, `docs/design/` |
| `SPEC-saathi-safety-2026-11-09-r2.md` | r2 | `7cea202752da682083102225f5a67cc0020f02b5cc6eb8043b284799b43cca6c` | 2026-11-09 | none yet | pending review | this repository, `docs/design/` |
| `SPEC-saathi-assurance-2026-11-09-r2.md` | r2 | `80e2a53976f6ab67428e5e608912574d2e1901c72df110c6346467abe5757739` | 2026-11-09 | none yet | pending review | this repository, `docs/design/` |
| `SPEC-saathi-evidence-2026-11-09-r2.md` | r2 | `adf2ec99f2231a89de6c7209fffb72189f176f6fac90b0cc98a484348c3549cc` | 2026-11-09 | none yet | pending review | this repository, `docs/design/` |
| `SPEC-saathi-alternatives-2026-11-09.md` | r2 set, new | `17e3e490009b57dadf3d6aba8b9e2efd91c92def1086dcc084f12df5ba0e30c8` | 2026-11-09 | none yet | pending review | this repository, `docs/design/` |
| `POLICY-saathi-2026-11-09-r2.md` | r2 | `1d90ef6591255a75974ad725c5ed1a3c0850a26d9e78e6461760709b38d63523` | 2026-11-09 | none yet | pending review | this repository, `docs/design/` |
| `POLICY-saathi-data-2026-11-09.md` | r2 set, new | `c992a27115ac658053e621c6aebff82171da8bb2bee724f360cbce72315eac5c` | 2026-11-09 | none yet | pending review | this repository, `docs/design/` |
| `RECORD-saathi-decisions-2026-11-09.md` | record, current | `2477aaa1613ac11185b8cab98444568033d541f6c45f27d3156db31ef4b27116` | 2026-11-09 | none yet | pending review | this repository, `docs/design/` |

The first three rows are the first drafts reviewed in round 1 (A and B); the next six are the r1 files reviewed in round 2 (C and D). Both rounds requested changes, and the r2 files answer the findings. No row records an owner lock. The old files are not in this repository; each r2 file records the hash it replaced in its `supersedes:` line.

Skipped or not checked: the review reports themselves are not stored here, so the results of the first nine rows are R, relayed by the orchestrating session; the r2 hashes were computed from the files in this folder on 2026-11-09 and nothing else was checked.
