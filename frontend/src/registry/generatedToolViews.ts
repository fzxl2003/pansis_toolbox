import { lazy } from 'react';

export const generatedToolViews = {
  "text_cleaner": lazy(() => import("../../../tools/text_cleaner/frontend/index")),
} as const;

export type GeneratedToolId = keyof typeof generatedToolViews;
