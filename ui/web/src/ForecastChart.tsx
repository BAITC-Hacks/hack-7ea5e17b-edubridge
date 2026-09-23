import { useMemo } from "react";
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { formatTime } from "./data";
import type { ForecastRecord } from "./data";
const turbineName = (id: string) =>
  ({ turbine_1: "Турбина 01", turbine_2: "Турбина 02" })[id] || id;
const number = (value: number) =>
  new Intl.NumberFormat("ru-RU", {
    maximumFractionDigits: 3,
    minimumFractionDigits: 3,
  }).format(value);
const compactDate = (value: string, zone: string) =>
  new Intl.DateTimeFormat("ru-RU", {
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
    timeZone: zone,
  }).format(new Date(value));

export default function ForecastChart({
  rows,
  zone,
}: {
  rows: ForecastRecord[];
  zone: string;
}) {
  const ids = [...new Set(rows.map((row) => row.turbine_id))];
  const data = useMemo(() => {
    const points = new Map<string, Record<string, string | number>>();
    for (const row of rows) {
      const point = points.get(row.valid_time) || { time: row.valid_time };
      point[row.turbine_id] = row.prediction;
      points.set(row.valid_time, point);
    }
    return [...points.values()].sort(
      (a, b) => Date.parse(String(a.time)) - Date.parse(String(b.time)),
    );
  }, [rows]);
  return (
    <div
      className="chart"
      role="img"
      aria-label="Почасовой прогноз выбранных турбин. Точные значения приведены в таблице ниже."
    >
      <ResponsiveContainer width="100%" height="100%">
        <LineChart
          data={data}
          margin={{ top: 18, right: 10, left: -26, bottom: 6 }}
          accessibilityLayer
        >
          <CartesianGrid
            stroke="#dce8e8"
            strokeDasharray="3 6"
            vertical={false}
          />
          <XAxis
            dataKey="time"
            minTickGap={52}
            axisLine={false}
            tickLine={false}
            tick={{ fill: "#586f78", fontSize: 11 }}
            tickFormatter={(value) => compactDate(String(value), zone)}
            dy={8}
          />
          <YAxis
            axisLine={false}
            tickLine={false}
            tick={{ fill: "#586f78", fontSize: 11 }}
            width={54}
            tickFormatter={(value) => String(Number(value.toFixed(2)))}
          />
          <Tooltip
            labelFormatter={(label) => formatTime(String(label), zone)}
            formatter={(value, name) => [
              number(Number(value)),
              turbineName(String(name)),
            ]}
            contentStyle={{
              border: "1px solid #dfebec",
              borderRadius: 14,
              fontSize: 12,
              boxShadow: "0 8px 30px #18474d12",
            }}
          />
          {ids.map((id, i) => (
            <Line
              key={id}
              type="monotone"
              dataKey={id}
              stroke={i ? "#83a8e4" : "#258895"}
              strokeWidth={2.7}
              dot={false}
              activeDot={{ r: 5, stroke: "#fff", strokeWidth: 2 }}
              isAnimationActive={false}
            />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
