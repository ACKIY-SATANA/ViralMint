// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (c) 2025-2026 ViralMint Contributors
/**
 * Survive browser page-translation.
 *
 * Chrome/Edge/Safari "translate this page" (and the Baidu/QQ equivalents)
 * rewrite text nodes into <font> wrappers, re-parenting nodes React still
 * tracks in its fibre tree. React's next update then calls removeChild /
 * insertBefore against a parent that is no longer the node's parent, the DOM
 * throws `NotFoundError: Failed to execute 'removeChild' on 'Node'`, and React
 * unmounts the tree to the nearest error boundary — for us the one wrapping
 * the whole router in App.jsx. See facebook/react#11538 (still open).
 *
 * Our chat surface is the worst case: streaming tokens and job cards mount and
 * unmount conditionally-rendered text nodes continuously, which is exactly the
 * trigger. index.html declares `lang="en"`, so a non-English speaker is offered
 * the translation on first load — and this app is self-hosted worldwide.
 *
 * The fix is to make those two Node methods tolerant of externally-moved nodes:
 * when the node isn't where React thinks it is, the translation layer has
 * already detached it, so the removal React wants has effectively happened —
 * return instead of throwing. Normal operation is untouched, because in normal
 * operation the parent always matches.
 *
 * Deliberately NOT a substitute for real i18n: this only stops the crash. It
 * does not translate anything.
 */

let installed = false

export function installTranslationGuard(target = typeof Node !== "undefined" ? Node : null) {
  if (installed || !target?.prototype) return false

  const originalRemoveChild = target.prototype.removeChild
  target.prototype.removeChild = function removeChild(child) {
    if (child?.parentNode !== this) {
      // Already detached by the translation layer — the caller's intent holds.
      return child
    }
    return originalRemoveChild.apply(this, arguments)
  }

  const originalInsertBefore = target.prototype.insertBefore
  target.prototype.insertBefore = function insertBefore(newNode, referenceNode) {
    if (referenceNode && referenceNode.parentNode !== this) {
      // The anchor moved; append instead of throwing. Ordering may be off by a
      // node in a translated tree — acceptable next to unmounting the app.
      return originalInsertBefore.call(this, newNode, null)
    }
    return originalInsertBefore.apply(this, arguments)
  }

  installed = true
  return true
}

// Test-only: the guard patches a global prototype, so suites need a way back.
export function __resetTranslationGuardForTests() {
  installed = false
}
