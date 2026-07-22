import { lazy } from 'react';

export const generatedToolViews = {
  "docker_manager": lazy(() => import("../../../tools/docker_manager/frontend/index")),
  "experiment_monitor": lazy(() => import("../../../tools/experiment_monitor/frontend/index")),
  "server_monitor": lazy(() => import("../../../tools/server_monitor/frontend/index")),
  "ssh_workspace": lazy(() => import("../../../tools/ssh_workspace/frontend/index")),
  "tensorboard_dashboard": lazy(() => import("../../../tools/tensorboard_dashboard/frontend/index")),
  "url_navigator": lazy(() => import("../../../tools/url_navigator/frontend/index")),
  "web_proxy": lazy(() => import("../../../tools/web_proxy/frontend/index")),
} as const;

export type GeneratedToolId = keyof typeof generatedToolViews;
