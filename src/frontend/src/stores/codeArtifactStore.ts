import { create } from 'zustand';
import type { ExecFileRef } from '../utils/codeExecParser';

export interface CodeArtifact {
  toolKey: string;
  code: string;
  language: string;
  stdout: string;
  stderr: string;
  exitCode: number;
  executionTimeMs: number;
  files: ExecFileRef[];
  isCommand?: boolean;
}

interface CodeArtifactState {
  isOpen: boolean;
  artifact: CodeArtifact | null;
  activeView: 'code' | 'preview';
  openSeq: number;
  openCodeArtifact: (artifact: CodeArtifact) => void;
  closeCodeArtifact: () => void;
  setActiveView: (view: 'code' | 'preview') => void;
  updateArtifact: (patch: Partial<CodeArtifact>) => void;
}

export const useCodeArtifactStore = create<CodeArtifactState>((set) => ({
  isOpen: false,
  artifact: null,
  activeView: 'code',
  openSeq: 0,
  openCodeArtifact: (artifact) => set((s) => ({
    isOpen: true,
    artifact,
    activeView: 'code',
    openSeq: s.openSeq + 1,
  })),
  closeCodeArtifact: () => set({ isOpen: false, artifact: null }),
  setActiveView: (view) => set({ activeView: view }),
  updateArtifact: (patch) => set((state) => ({
    artifact: state.artifact ? { ...state.artifact, ...patch } : null,
  })),
}));
