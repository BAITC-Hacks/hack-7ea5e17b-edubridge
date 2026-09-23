/** Require a real ISO calendar date and an explicit offset; never assume a timezone. */
export function timestamp(value: unknown): number {
  if (typeof value !== "string")
    throw new Error("Некорректная дата в ответе сервиса.");
  const parts =
    /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2})(?:\.(\d+))?)?(Z|[+-]\d{2}:\d{2})$/i.exec(
      value,
    );
  if (!parts) throw new Error("Дата должна содержать время и часовой пояс.");
  const [, year, month, day, hour, minute, second = "0"] = parts;
  const maxDay = new Date(
    Date.UTC(Number(year), Number(month), 0),
  ).getUTCDate();
  if (
    Number(month) < 1 ||
    Number(month) > 12 ||
    Number(day) < 1 ||
    Number(day) > maxDay ||
    Number(hour) > 23 ||
    Number(minute) > 59 ||
    Number(second) > 59
  ) {
    throw new Error("Некорректная календарная дата в ответе сервиса.");
  }
  const result = Date.parse(value);
  if (!Number.isFinite(result))
    throw new Error("Некорректная дата в ответе сервиса.");
  return result;
}
