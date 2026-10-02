import { lazy } from 'react';

export const generatedToolViews = {
  "airplay_subscription_manager": lazy(() => import("../../../tools/airplay_subscription_manager/frontend/index")),
  "docker_manager": lazy(() => import("../../../tools/docker_manager/frontend/index")),
  "experiment_monitor": lazy(() => import("../../../tools/experiment_monitor/frontend/index")),
  "git_blog": lazy(() => import("../../../tools/git_blog/frontend/index")),
  "server_monitor": lazy(() => import("../../../tools/server_monitor/frontend/index")),
  "ssh_workspace": lazy(() => import("../../../tools/ssh_workspace/frontend/index")),
  "tensorboard_dashboard": lazy(() => import("../../../tools/tensorboard_dashboard/frontend/index")),
  "tensorboard_progress_monitor": lazy(() => import("../../../tools/tensorboard_progress_monitor/frontend/index")),
  "url_navigator": lazy(() => import("../../../tools/url_navigator/frontend/index")),
  "web_proxy": lazy(() => import("../../../tools/web_proxy/frontend/index")),
} as const;

export type GeneratedToolId = keyof typeof generatedToolViews;
