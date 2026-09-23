import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { formatTime } from "./data";
import { appMessages } from "./locales/app";
import { evaluationMessages } from "./locales/evaluation";
import { analysisMessages } from "./locales/analysis";
import { runtimeMessages, runtimePatterns } from "./locales/messages";

export type Locale = "ru" | "kk" | "en";
export type MessageParams = Record<string, string | number>;
type Translation = { ru?: string; kk: string; en: string };
export const LOCALE_STORAGE_KEY = "jelai.locale";
export const intlLocales: Record<Locale, string> = {
  ru: "ru-RU", kk: "kk-KZ", en: "en-GB",
};
export const commonMessages: Record<string, Translation> = {
  "Не предоставлено": { kk: "Берілмеген", en: "Not provided" },
  "Некорректное время": { kk: "Уақыт дұрыс емес", en: "Invalid time" },
  "Турбина 01": { kk: "Турбина 01", en: "Turbine 01" },
  "Турбина 02": { kk: "Турбина 02", en: "Turbine 02" },
  "JelAI — энергия ветра под вашим контролем": {
    kk: "JelAI — жел энергиясы сіздің бақылауыңызда",
    en: "JelAI — wind energy under your control",
  },
  "JelAI — почасовой прогноз ветроэнергии с проверяемым происхождением данных.": {
    kk: "JelAI — дереккөздері тексерілетін жел энергиясының сағаттық болжамы.",
    en: "JelAI — hourly wind energy forecasts with verifiable data sources.",
  },
};
export const messages: Record<string, Translation> = {
  ...commonMessages, ...appMessages, ...evaluationMessages, ...analysisMessages, ...runtimeMessages,
};

export function isLocale(value: unknown): value is Locale {
  return value === "ru" || value === "kk" || value === "en";
}

export function translate(source: string, locale: Locale, params: MessageParams = {}): string {
  const text = messages[source]?.[locale] ?? source;
  return text.replace(/\{(\w+)\}/g, (token, key: string) =>
    Object.prototype.hasOwnProperty.call(params, key) ? String(params[key]) : token,
  );
}

/** Translate known user messages without rewriting identifiers or unknown service details. */
export function translateMessage(source: string, locale: Locale): string {
  if (Object.prototype.hasOwnProperty.call(messages, source)) return translate(source, locale);
  for (const { pattern, key, params } of runtimePatterns) {
    const match = source.match(pattern);
    if (match) {
      const values = params(match);
      // HTTP errors can wrap another known message from the service.
      if (values.detail) {
        const original = values.detail;
        const detail = original.trim();
        values.detail = detail
          ? `${original.match(/^\s*/)?.[0] ?? ""}${translate(detail, locale)}${original.match(/\s*$/)?.[0] ?? ""}`
          : original;
      }
      return translate(key, locale, values);
    }
  }
  return source;
}

function storedLocale(): Locale {
  try {
    const saved = window.localStorage.getItem(LOCALE_STORAGE_KEY);
    return isLocale(saved) ? saved : "ru";
  } catch {
    return "ru";
  }
}

function formatters(locale: Locale) {
  const intlLocale = intlLocales[locale];
  return {
    locale, intlLocale,
    t: (source: string, params?: MessageParams) => translate(source, locale, params),
    message: (source: string) => translateMessage(source, locale),
    number: (value: number | undefined, digits = 3) => value === undefined ? "—" :
      new Intl.NumberFormat(intlLocale, {
        minimumFractionDigits: digits, maximumFractionDigits: digits,
      }).format(value),
    time: (value: string | null | undefined, zone = "UTC") =>
      translate(formatTime(value, zone, intlLocale), locale),
    turbineName: (id: string) => translate(
      ({ turbine_1: "Турбина 01", turbine_2: "Турбина 02" } as Record<string, string>)[id] || id,
      locale,
    ),
  };
}

type I18n = ReturnType<typeof formatters> & { setLocale: (locale: Locale) => void };
// Components embedded independently (including existing tests) retain the default Russian UI.
const I18nContext = createContext<I18n>({ ...formatters("ru"), setLocale: () => {} });

export function I18nProvider({ children }: { children: ReactNode }) {
  const [locale, updateLocale] = useState<Locale>(storedLocale);
  const setLocale = useCallback((next: Locale) => {
    if (isLocale(next)) updateLocale(next);
  }, []);
  useEffect(() => {
    try { window.localStorage.setItem(LOCALE_STORAGE_KEY, locale); } catch {
      // Browser privacy/storage restrictions must not prevent switching languages.
    }
    document.documentElement.lang = locale;
    document.title = translate("JelAI — энергия ветра под вашим контролем", locale);
    document.querySelector('meta[name="description"]')?.setAttribute("content",
      translate("JelAI — почасовой прогноз ветроэнергии с проверяемым происхождением данных.", locale),
    );
  }, [locale]);
  const value = useMemo(() => ({ ...formatters(locale), setLocale }), [locale, setLocale]);
  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

export const useI18n = () => useContext(I18nContext);
