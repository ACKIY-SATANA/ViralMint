// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (c) 2025-2026 ViralMint Contributors
import { useState, useCallback } from "react"
import {
  Dialog, DialogTitle, DialogContent, DialogActions, Button, Typography,
} from "@mui/material"

// Replaces the local confirmDialog/openConfirm/closeConfirm scaffolding
// that Chat / Videos / ClipStudio each had inline. Same external shape
// — destructive "Cancel" / "Delete" by default — but every caller now
// shares the same modal.
//
// Usage:
//   const { confirm, ConfirmDialog } = useConfirm()
//   ...
//   <Button onClick={() => confirm(
//     "Delete chat?",
//     "This conversation will be permanently removed.",
//     handleDelete,
//   )}>Delete</Button>
//   ...
//   <ConfirmDialog />   {/* mount once at the bottom of the page */}
//
// Optional fourth argument lets a caller override the confirm-button
// label / color (e.g. "Discard" / "warning") if a future flow isn't
// destructive. Defaults match the existing hard-coded behavior so the
// migration is no-op for current callers.
export default function useConfirm() {
  const [state, setState] = useState({
    open: false,
    title: "",
    message: "",
    onConfirm: null,
    confirmLabel: "Delete",
    confirmColor: "error",
  })

  const close = useCallback(() => {
    setState((s) => ({ ...s, open: false }))
  }, [])

  const confirm = useCallback((title, message, onConfirm, opts = {}) => {
    setState({
      open: true,
      title,
      message,
      onConfirm,
      confirmLabel: opts.confirmLabel ?? "Delete",
      confirmColor: opts.confirmColor ?? "error",
    })
  }, [])

  // Inline dialog component bound to this hook's state. The caller
  // mounts <ConfirmDialog /> once and triggers it via confirm().
  const ConfirmDialog = useCallback(() => (
    <Dialog open={state.open} onClose={close} maxWidth="xs" fullWidth>
      <DialogTitle>{state.title}</DialogTitle>
      <DialogContent><Typography>{state.message}</Typography></DialogContent>
      <DialogActions>
        <Button onClick={close}>Cancel</Button>
        <Button
          color={state.confirmColor}
          variant="contained"
          onClick={() => { state.onConfirm?.(); close() }}
        >
          {state.confirmLabel}
        </Button>
      </DialogActions>
    </Dialog>
  ), [state, close])

  return { confirm, ConfirmDialog }
}
