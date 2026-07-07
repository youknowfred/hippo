---
name: unicode-normalization-nfc-vs-nfd
description: "compare unicode strings after NFC normalization or visually identical text can fail equality"
metadata:
  type: reference
---

A precomposed accented character (NFC) and its decomposed base+combining-mark form
(NFD) render identically but compare unequal byte-for-byte without normalizing first.
