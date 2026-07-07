---
name: hermetic-tests-no-network-no-clock
description: "a hermetic test never touches the network, real clock, or shared filesystem state"
metadata:
  type: project
---

Pin time via an injected `now`, fake network calls, and always operate inside a
tmp_path fixture — this is what makes a suite reproducible on a laptop or in CI alike.
