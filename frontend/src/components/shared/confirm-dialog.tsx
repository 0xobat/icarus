"use client";

import { motion, AnimatePresence } from "motion/react";

interface ConfirmDialogProps {
  open: boolean;
  title: string;
  description: string;
  confirmLabel?: string;
  onConfirm: () => void;
  onCancel: () => void;
}

export function ConfirmDialog({ open, title, description, confirmLabel = "Confirm", onConfirm, onCancel }: ConfirmDialogProps) {
  return (
    <AnimatePresence>
      {open && (
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm"
          onClick={onCancel}
        >
          <motion.div
            initial={{ scale: 0.95, opacity: 0 }}
            animate={{ scale: 1, opacity: 1 }}
            exit={{ scale: 0.95, opacity: 0 }}
            onClick={(e) => e.stopPropagation()}
            className="w-full max-w-sm rounded-lg border border-border-default bg-bg-surface p-6"
          >
            <h3 className="font-display text-sm font-bold text-text-primary">{title}</h3>
            <p className="mt-2 text-xs text-text-primary">{description}</p>
            <div className="mt-4 flex justify-end gap-2">
              <button
                onClick={onCancel}
                className="rounded px-3 py-1.5 font-mono text-xs text-text-secondary border border-border-subtle hover:bg-bg-hover transition-colors"
              >
                Cancel
              </button>
              <button
                onClick={onConfirm}
                className="rounded px-3 py-1.5 font-mono text-xs text-white bg-danger hover:bg-danger/80 transition-colors"
              >
                {confirmLabel}
              </button>
            </div>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
