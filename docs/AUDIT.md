# Architecture audit before implementation

Base: origin/alpha_test; local branch research_gui_trend_gap.
The initial main checkout contained only README/LICENSE. alpha_test contains the working PoC.
User .idea files are preserved and excluded from commits.

Reusable: MetricEngine and MetricConfig implement Gap mathematics; MetricsState,
RawBatchStore, CSV writer, validator, dataset builder and their tests remain supported.
Current collector couples search to creator eligibility, loses ineligible observations,
uses one page, and has a hardcoded lower publication date. HTTP errors can expose
request details; no retry, quota guard, profile selection or GUI exists.

Proposed: research domain contracts -> shared discovery -> independent enrichment
-> existing Gap engine / observational Trend engine -> append-only topic storage.
Application services orchestrate experiments, scheduling and offline replay.
PySide6 presentation delegates work to a worker thread and plots stored results.
Known events belong exclusively to evaluation. Raw schema 2 is explicitly separate
from legacy schema 1; original CLI and old replay remain compatible.

Planned new files: research/{domain,profiles,quota,storage,discovery,gap,trend,
tracking,scheduling,application,replay,evaluation,gui}.py, research/__main__.py,
config/formulas/*.json, tests/test_research*.py, docs architecture/developer guides.
Modified files: youtube/client.py, search.py, videos.py, config.py, requirements.txt,
README.md, .gitignore. Existing Gap calculators will not be changed.

Risks: no Trend mathematical specification is present in this repository. Score,
G/Z/E/B/confidence formula and lifecycle thresholds cannot be claimed as official;
expose observed activity/counter metrics and mark unsupported outputs unavailable.
Historical publication search only returns present-day counters; never backdate them.
Search caps and partial failures are sampling limitations, not zero activity.
Quota is a local estimate shared by selected profile; external usage is unknowable.
No remote mutations, pushes or PRs are permitted.

## Specification resolved during audit
The user supplied MathCore/CreatorRadar_TrendEngine_Diploma_Expanded.ipynb. Its formulas and explicit YouTube research adaptations are documented in FORMULAS.md. The earlier missing-specification risk was resolved; the production multi-platform input limitation remains.
