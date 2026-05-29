import { lazy } from 'react';

export const generatedToolViews = {
  "memo_demo": lazy(() => import("../../../tools/memo_demo/frontend/index")),
  "text_cleaner": lazy(() => import("../../../tools/text_cleaner/frontend/index")),
} as const;

export type GeneratedToolId = keyof typeof generatedToolViews;
