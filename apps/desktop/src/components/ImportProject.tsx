import { open } from "@tauri-apps/plugin-dialog";
import FolderOpen from "lucide-react/icons/folder-open";
import LoaderCircle from "lucide-react/icons/loader-circle";
import { useId, useState } from "react";

import { EngineApiError } from "../api/engine";
import {
  createProject,
  type ProjectSummary,
} from "../api/projects";

type ImportProjectProps = {
  onImported?: (project: ProjectSummary) => void | Promise<void>;
  compact?: boolean;
};

const AUDIO_FILTERS = [
  {
    name: "音频文件",
    extensions: ["wav", "mp3", "flac", "m4a", "aac", "ogg", "opus", "wma"],
  },
];

export function ImportProject({ onImported, compact = false }: ImportProjectProps) {
  const [pending, setPending] = useState(false);
  const errorId = useId();
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  async function chooseLocalFile() {
    setErrorMessage(null);
    try {
      const sourcePath = await open({
        title: "选择音频",
        multiple: false,
        directory: false,
        filters: AUDIO_FILTERS,
      });
      if (typeof sourcePath !== "string") return;

      const outputDir = await open({
        title: "选择项目保存位置",
        multiple: false,
        directory: true,
        recursive: true,
      });
      if (typeof outputDir !== "string") return;

      setPending(true);
      const project = await createProject({ sourcePath, outputDir });
      await onImported?.(project);
    } catch (error) {
      setErrorMessage(
        error instanceof EngineApiError
          ? error.message
          : "无法创建本机项目，请检查文件与输出位置",
      );
    } finally {
      setPending(false);
    }
  }

  return (
    <div className={compact ? "import-project import-project--compact" : "import-project"}>
      <button
        className={compact ? "button button--sidebar" : "button button--primary"}
        type="button"
        data-guide="import"
        onClick={() => void chooseLocalFile()}
        disabled={pending}
        aria-describedby={errorMessage ? errorId : undefined}
      >
        {pending ? <LoaderCircle aria-hidden="true" /> : <FolderOpen aria-hidden="true" />}
        {pending ? "正在创建项目…" : "选择本机文件"}
      </button>
      {errorMessage ? (
        <p className="inline-error" id={errorId} role="alert">
          {errorMessage}
        </p>
      ) : null}
    </div>
  );
}
