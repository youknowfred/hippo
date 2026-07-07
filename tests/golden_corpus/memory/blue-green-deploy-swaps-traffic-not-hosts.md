---
name: blue-green-deploy-swaps-traffic-not-hosts
description: "blue-green deployment keeps two full environments and flips a router, not individual servers"
metadata:
  type: reference
---

The old (blue) environment stays warm as an instant rollback target after the
traffic switch to green — rollback is a router flip, not a redeploy.
