/**
 * Mock project-file fixtures used by the Files tab until the backend
 * grows a `GET /api/v1/projects/{id}/files` endpoint.
 *
 * The sample set mirrors the Claude Design prototype's seed data so the
 * viewer can be exercised visually (admin dashboard .bsd with KPIs +
 * chart + row + pill, an empty-state .bsd, plus .md / .ts / .json /
 * url).
 */
import type { ProjectFile } from './bsd-types'

const ADMIN_DASHBOARD: ProjectFile = {
  path: 'deliverables/admin-dashboard.bsd',
  type: 'bsd',
  size: '12.4 kB',
  bsd: {
    meta: { name: 'Admin metering dashboard', version: '0.3', fps: 60 },
    tokens: {
      colors: {
        bg: '#0a0b0f',
        surf: '#111218',
        text: '#e4e6ee',
        accent: '#3b82f6',
        muted: '#8187a8',
      },
      radius: { sm: 4, md: 8, lg: 12 },
    },
    frames: [
      {
        id: 'f_home',
        name: 'Home',
        w: 1200,
        h: 720,
        bg: 'bg',
        layers: [
          { t: 'rect', x: 0, y: 0, w: 1200, h: 56, fill: 'surf' },
          { t: 'text', x: 24, y: 20, text: 'Usage — All workspaces', font: '600 15px sans', fill: 'text' },
          { t: 'rect', x: 1080, y: 14, w: 96, h: 28, fill: 'accent', r: 'md' },
          { t: 'text', x: 1100, y: 32, text: 'Export CSV', font: '500 12px sans', fill: '#fff' },
          {
            t: 'group',
            name: 'KPIs',
            x: 24,
            y: 80,
            children: [
              { t: 'kpi', x: 0, y: 0, w: 268, h: 96, label: 'API calls · 7d', value: '12.4M', delta: '+18%' },
              { t: 'kpi', x: 284, y: 0, w: 268, h: 96, label: 'Active seats', value: '3,412', delta: '+4%' },
              { t: 'kpi', x: 568, y: 0, w: 268, h: 96, label: 'Overages', value: '27', delta: '+9' },
              { t: 'kpi', x: 852, y: 0, w: 300, h: 96, label: 'Spend (MTD)', value: '$48,210', delta: '+11%' },
            ],
          },
          { t: 'rect', x: 24, y: 200, w: 1152, h: 280, fill: 'surf', r: 'lg' },
          { t: 'text', x: 40, y: 228, text: 'API calls by day', font: '600 13px sans', fill: 'text' },
          {
            t: 'chart',
            x: 40,
            y: 252,
            w: 1120,
            h: 210,
            kind: 'line',
            series: [120, 180, 150, 220, 260, 340, 420, 390, 510, 620, 580, 700, 640, 820],
          },
          { t: 'rect', x: 24, y: 496, w: 752, h: 200, fill: 'surf', r: 'lg' },
          { t: 'text', x: 40, y: 524, text: 'Top workspaces', font: '600 13px sans', fill: 'text' },
          {
            t: 'row',
            x: 40,
            y: 552,
            w: 712,
            data: [
              { ws: 'acme-corp', calls: '3.2M', spend: '$12,840' },
              { ws: 'zenith', calls: '2.1M', spend: '$8,310' },
              { ws: 'blueox', calls: '1.8M', spend: '$7,120' },
              { ws: 'ridgeline', calls: '1.4M', spend: '$5,600' },
            ],
          },
          { t: 'rect', x: 792, y: 496, w: 384, h: 200, fill: 'surf', r: 'lg' },
          { t: 'text', x: 808, y: 524, text: 'Hard-cap status', font: '600 13px sans', fill: 'text' },
          { t: 'pill', x: 808, y: 548, label: 'Soft throttle', tone: 'amber' },
          { t: 'text', x: 808, y: 596, text: '27 workspaces over 80% of cap', font: '400 12px sans', fill: 'muted' },
          { t: 'text', x: 808, y: 620, text: '4 currently throttled', font: '400 12px sans', fill: 'muted' },
        ],
      },
      {
        id: 'f_detail',
        name: 'Workspace detail',
        w: 1200,
        h: 720,
        bg: 'bg',
        layers: [
          { t: 'rect', x: 0, y: 0, w: 1200, h: 56, fill: 'surf' },
          { t: 'text', x: 24, y: 20, text: 'acme-corp · usage', font: '600 15px sans', fill: 'text' },
          { t: 'rect', x: 24, y: 80, w: 1152, h: 600, fill: 'surf', r: 'lg' },
          {
            t: 'chart',
            x: 40,
            y: 120,
            w: 1120,
            h: 260,
            kind: 'area',
            series: [240, 180, 260, 310, 290, 420, 380, 460, 510, 480, 520, 610, 640, 720],
          },
          { t: 'text', x: 40, y: 400, text: 'Events (last 24h)', font: '600 13px sans', fill: 'text' },
          {
            t: 'row',
            x: 40,
            y: 432,
            w: 1120,
            data: [
              { ts: '14:02', ev: 'api.invoke', wid: 'wsp_a1', ok: '200' },
              { ts: '14:02', ev: 'seat.added', wid: 'wsp_a1', ok: '200' },
              { ts: '14:01', ev: 'api.invoke', wid: 'wsp_a1', ok: '200' },
              { ts: '14:00', ev: 'api.invoke', wid: 'wsp_a1', ok: '429' },
            ],
          },
        ],
      },
    ],
  },
}

const EMPTY_STATE: ProjectFile = {
  path: 'deliverables/empty-state.bsd',
  type: 'bsd',
  size: '3.1 kB',
  bsd: {
    meta: { name: 'Empty state', version: '0.1' },
    tokens: {
      colors: {
        bg: '#0a0b0f',
        surf: '#111218',
        text: '#e4e6ee',
        muted: '#8187a8',
        accent: '#3b82f6',
      },
      radius: { sm: 4, md: 8, lg: 12 },
    },
    frames: [
      {
        id: 'f_empty',
        name: 'Empty',
        w: 800,
        h: 520,
        bg: 'bg',
        layers: [
          { t: 'rect', x: 40, y: 40, w: 720, h: 440, fill: 'surf', r: 'lg' },
          {
            t: 'text',
            x: 400,
            y: 220,
            text: 'No workspaces yet',
            font: '600 20px sans',
            fill: 'text',
            align: 'center',
          },
          {
            t: 'text',
            x: 400,
            y: 252,
            text: 'Invite teammates or import from Stripe.',
            font: '400 13px sans',
            fill: 'muted',
            align: 'center',
          },
          { t: 'rect', x: 330, y: 290, w: 140, h: 36, fill: 'accent', r: 'md' },
          {
            t: 'text',
            x: 400,
            y: 312,
            text: 'Invite team',
            font: '500 13px sans',
            fill: '#fff',
            align: 'center',
          },
        ],
      },
    ],
  },
}

const METERING_SCHEMA: ProjectFile = {
  path: 'deliverables/metering-schema.md',
  type: 'md',
  size: '6.8 kB',
  content: `# Metering schema v1

## Overview
Events are ingested via Kafka, aggregated daily into ClickHouse.

## Tables

### \`events_raw\`
| col | type | note |
|---|---|---|
| ts | DateTime | event time |
| workspace_id | String | FK |
| event_type | LowCardinality(String) | api.invoke, seat.added |
| payload | JSON | opaque |

### \`events_daily\`
Partitioned by day. Rollup via \`MaterializedView\`.

## Retention
Raw: **30 days**. Rollups: **1 year**.
`,
}

const INGEST_WORKER: ProjectFile = {
  path: 'services/ingest/worker.ts',
  type: 'code',
  lang: 'ts',
  size: '4.2 kB',
  content: `import { Kafka } from "kafkajs";
import { client as ch } from "./clickhouse";

const kafka = new Kafka({
  clientId: "metering-ingest",
  brokers: process.env.KAFKA_BROKERS!.split(","),
});

const consumer = kafka.consumer({ groupId: "metering-ingest-v1" });

export async function run() {
  await consumer.connect();
  await consumer.subscribe({ topics: ["events.raw"], fromBeginning: false });

  await consumer.run({
    eachBatch: async ({ batch, resolveOffset, heartbeat }) => {
      const rows = batch.messages.map((m) => ({
        ts: new Date(Number(m.timestamp)),
        workspace_id: m.key!.toString(),
        event_type: m.headers?.event_type?.toString() ?? "unknown",
        payload: m.value!.toString(),
      }));
      await ch.insert({ table: "events_raw", values: rows, format: "JSONEachRow" });
      batch.messages.forEach((m) => resolveOffset(m.offset));
      await heartbeat();
    },
  });
}
`,
}

const SAMPLE_CONFIG: ProjectFile = {
  path: 'config/pricing.json',
  type: 'data',
  size: '1.1 kB',
  content: `{
  "plans": {
    "starter": { "monthly": 49, "included_calls": 50000 },
    "growth":  { "monthly": 299, "included_calls": 1000000 },
    "scale":   { "monthly": 1499, "included_calls": 10000000 }
  },
  "overage_cents_per_1k": 4
}
`,
}

const LIVE_METRICS_LINK: ProjectFile = {
  path: 'links/live-metrics.url',
  type: 'url',
  size: '—',
  content: 'https://grafana.internal/d/api-latency',
}

export const SAMPLE_FILES: ProjectFile[] = [
  ADMIN_DASHBOARD,
  EMPTY_STATE,
  METERING_SCHEMA,
  INGEST_WORKER,
  SAMPLE_CONFIG,
  LIVE_METRICS_LINK,
]
