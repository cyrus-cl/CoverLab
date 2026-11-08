import FileAudio from "lucide-react/icons/file-audio";
import Search from "lucide-react/icons/search";
import { useDeferredValue, useId, useState } from "react";

import type { ProjectSummary } from "../api/projects";
import { ImportProject } from "./ImportProject";
import { BrandLockup } from "./BrandLockup";

type ProjectSidebarProps = {
  projects: ProjectSummary[];
  selectedId: string | null;
  onSelect: (projectId: string) => void;
  onImported: (project: ProjectSummary) => void | Promise<void>;
  loading: boolean;
};

export function ProjectSidebar({
  projects,
  selectedId,
  onSelect,
  onImported,
  loading,
}: ProjectSidebarProps) {
  const [search, setSearch] = useState("");
  const searchId = useId();
  const deferredSearch = useDeferredValue(search.trim().toLocaleLowerCase());
  const visibleProjects = deferredSearch
    ? projects.filter((project) =>
        `${project.name} ${fileName(project.sourcePath)} ${project.outputDir}`
          .toLocaleLowerCase()
          .includes(deferredSearch),
      )
    : projects;

  const projectNameCounts = new Map<string, number>();
  for (const project of projects) {
    const name = project.name.toLocaleLowerCase();
    projectNameCounts.set(name, (projectNameCounts.get(name) ?? 0) + 1);
  }

  return (
    <aside className="project-sidebar" aria-label="项目历史">
      <BrandLockup />
      <nav className="sidebar-navigation" aria-label="工作区导航">
        <a href="#workspace-main" aria-current="page"><FileAudio aria-hidden="true" />翻唱创作</a>
      </nav>
      <div className="sidebar-heading">
        <div>
          <h2>最近项目</h2>
        </div>
        <span className="project-count">{projects.length}</span>
      </div>

      {projects.length > 0 ? <ImportProject compact onImported={onImported} /> : null}

      <div className="project-search">
        <Search aria-hidden="true" />
        <label className="sr-only" htmlFor={searchId}>搜索项目</label>
        <input
          id={searchId}
          type="search"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          placeholder="搜索项目"
        />
        {search ? <button type="button" className="project-search-clear" aria-label="清空项目搜索" onClick={() => setSearch("")}>×</button> : null}
      </div>

      <div className="project-list" aria-live="polite">
        {loading ? <ProjectListSkeleton /> : null}
        {!loading && projects.length === 0 ? (
          <div className="sidebar-empty">
            <FileAudio aria-hidden="true" />
            <p>暂无项目</p>
          </div>
        ) : null}
        {!loading && projects.length > 0 && visibleProjects.length === 0 ? (
          <p className="sidebar-empty-copy">没有匹配的项目</p>
        ) : null}
        {visibleProjects.map((project) => (
          <button
            className={`project-row${project.id === selectedId ? " is-active" : ""}`}
            type="button"
            key={project.id}
            onClick={() => onSelect(project.id)}
            aria-pressed={project.id === selectedId}
            title={project.outputDir}
          >
            <span className="project-row__icon"><img src="/brand/vinyl.png" width="36" height="36" alt="" /></span>
            <span className="project-row__copy">
              <strong>{project.name}</strong>
              {(projectNameCounts.get(project.name.toLocaleLowerCase()) ?? 0) > 1 ? (
                <small className="project-row__location">
                  {fileName(project.outputDir.replace(/[\\/]+$/, ""))} · {createdLabel(project.createdAt)}
                </small>
              ) : null}
            </span>
          </button>
        ))}
      </div>

      <div className="sidebar-footer">
        <img className="sidebar-still" src="/brand/coverlab-studio-still-v1.png" width="2172" height="724" alt="" aria-hidden="true" />
      </div>
    </aside>
  );
}

function ProjectListSkeleton() {
  return (
    <div className="project-skeleton" aria-label="正在读取本机项目" role="status">
      <span />
      <span />
      <span />
    </div>
  );
}

function fileName(path: string): string {
  return path.split(/[\\/]/).pop() || path;
}

function createdLabel(value:string):string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "时间未记录" : new Intl.DateTimeFormat("zh-CN",{month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit",hour12:false}).format(date);
}
