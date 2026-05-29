import { apiGet, apiPut } from './client';

export type Widget = {
  id: string;
  toolId: string;
  name: string;
  type: string;
  defaultSize: { w: number; h: number };
  toolStatus: string;
};

export type WidgetData = {
  widgetId: string;
  type: string;
  title: string;
  data: Record<string, unknown>;
  updatedAt: string;
};

export type WidgetLayout = {
  userId: string;
  widgets: Array<{ id: string; x: number; y: number; w: number; h: number; enabled: boolean }>;
};

export function fetchWidgets() {
  return apiGet<Widget[]>('/api/widgets');
}

export function fetchWidgetData(widgetId: string) {
  return apiGet<WidgetData>(`/api/widgets/${widgetId}/data`);
}

export function fetchWidgetLayout() {
  return apiGet<WidgetLayout>('/api/widgets/layout');
}

export function saveWidgetLayout(layout: WidgetLayout) {
  return apiPut<WidgetLayout>('/api/widgets/layout', layout);
}
