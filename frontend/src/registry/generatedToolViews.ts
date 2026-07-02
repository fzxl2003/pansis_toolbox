import { lazy } from 'react';

export const generatedToolViews = {
  "docker_manager": lazy(() => import("../../../tools/docker_manager/frontend/index")),
  "experiment_monitor": lazy(() => import("../../../tools/experiment_monitor/frontend/index")),
  "memo_demo": lazy(() => import("../../../tools/memo_demo/frontend/index")),
  "server_monitor": lazy(() => import("../../../tools/server_monitor/frontend/index")),
  "ssh_workspace": lazy(() => import("../../../tools/ssh_workspace/frontend/index")),
  "text_cleaner": lazy(() => import("../../../tools/text_cleaner/frontend/index")),
  "url_navigator": lazy(() => import("../../../tools/url_navigator/frontend/index")),
  "web_proxy": lazy(() => import("../../../tools/web_proxy/frontend/index")),
} as const;

export type GeneratedToolId = keyof typeof generatedToolViews;
