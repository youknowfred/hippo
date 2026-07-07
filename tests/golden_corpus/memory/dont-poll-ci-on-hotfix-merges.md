---
name: dont-poll-ci-on-hotfix-merges
description: "an urgent hotfix with green required checks can merge without babysitting every remaining job"
metadata:
  type: feedback
---

Once the required status checks report green, merge — don't sit refreshing the
Actions tab waiting for optional/slow jobs that aren't required to pass.
