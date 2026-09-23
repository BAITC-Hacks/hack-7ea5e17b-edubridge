/** Advisory diagnostics; translations do not alter reports or forecast values. */
export const analysisMessages: Record<string, { ru?: string; kk: string; en: string }> = {
  "Анализ агента": { kk: "Агент талдауы", en: "Agent analysis" },
  "Диагностика прогноза · ревизия {revision} · не оценка точности": {
    kk: "Болжам диагностикасы · {revision}-ревизия · дәлдікті бағалау емес",
    en: "Forecast diagnostics · revision {revision} · not an accuracy evaluation",
  },
  "Нужна проверка входов": { kk: "Кіріс деректерін тексеру қажет", en: "Review inputs" },
  "Наблюдать обновления": { kk: "Жаңартуларды бақылау", en: "Monitor updates" },
  "Синтетический fixture: анализ не описывает реальный эксплуатационный прогноз.": {
    kk: "Синтетикалық fixture: талдау нақты жұмыс болжамын сипаттамайды.",
    en: "Synthetic fixture: this analysis does not describe a real operational forecast.",
  },
  "Анализ: {turbine}": { kk: "Талдау: {turbine}", en: "Analysis: {turbine}" },
  "Минимум / максимум": { kk: "Ең аз / ең көп", en: "Minimum / maximum" },
  "Среднее": { kk: "Орташа мән", en: "Mean" },
  "Макс. изменение за час": { kk: "Бір сағаттағы ең үлкен өзгеріс", en: "Largest hourly change" },
  "Конец часа изменения": { kk: "Өзгеріс сағатының соңы", en: "Change interval end" },
  "Постоянный прогноз ≥ 24 ч": { kk: "Тұрақты болжам ≥ 24 сағ", en: "Constant forecast ≥ 24 h" },
  "Да — проверить входы": { kk: "Иә — кіріс деректерін тексеру", en: "Yes — review inputs" },
  "Не обнаружен": { kk: "Анықталмады", en: "Not detected" },
  "Ветер, мин. / макс., м/с": { kk: "Жел, ең аз / ең көп, м/с", en: "Wind, min. / max., m/s" },
  "Средний ветер, м/с": { kk: "Желдің орташа жылдамдығы, м/с", en: "Mean wind speed, m/s" },
  "Изменение к прошлой ревизии, среднее / максимум": {
    kk: "Алдыңғы ревизиядан өзгеріс, орташа / ең көп",
    en: "Change from previous revision, mean / maximum",
  },
  "Сравнение ревизий: общих часов {common}, изменено {changed}. Это разница прогнозов, не ошибка относительно факта.": {
    kk: "Ревизияларды салыстыру: ортақ сағаттар {common}, өзгергені {changed}. Бұл болжамдар айырмасы, нақты мәнге қатысты қате емес.",
    en: "Revision comparison: {common} common hours, {changed} changed. This is a difference between predictions, not an error against observations.",
  },
  "Сравнение с предыдущей ревизией недоступно для сопоставимых часов.": {
    kk: "Салыстыруға болатын сағаттар үшін алдыңғы ревизиямен салыстыру қолжетімсіз.",
    en: "A comparison with the previous revision is unavailable for comparable hours.",
  },
  "Фактическая выработка здесь не используется. Это рекомендация проверить входы или наблюдать обновления; она не меняет прогноз. Для величины скачков порог тревоги не установлен.": {
    kk: "Мұнда нақты өндірілген қуат қолданылмайды. Бұл кіріс деректерін тексеру немесе жаңартуларды бақылау туралы ұсыныс; ол болжамды өзгертпейді. Күрт өзгеріс шамасына дабыл шегі белгіленбеген.",
    en: "Observed generation is not used here. This recommends reviewing inputs or monitoring updates; it does not change the forecast. No alarm threshold is established for ramp magnitudes.",
  },
  "Полный анализ и ограничения": { kk: "Толық талдау және шектеулер", en: "Full analysis and limitations" },
  "Model or input warnings require review; see model_warnings.": {
    ru: "Предупреждения модели или входных данных требуют проверки; см. model_warnings в полном отчёте.",
    kk: "Модель немесе кіріс деректері туралы ескертулерді тексеру қажет; толық есептегі model_warnings бөлімін қараңыз.",
    en: "Model or input warnings require review; see model_warnings in the full report.",
  },
  "SYNTHETIC FIXTURE: diagnostics do not describe a real operational forecast.": {
    ru: "СИНТЕТИЧЕСКИЙ FIXTURE: диагностика не описывает реальный эксплуатационный прогноз.",
    kk: "СИНТЕТИКАЛЫҚ FIXTURE: диагностика нақты жұмыс болжамын сипаттамайды.",
    en: "SYNTHETIC FIXTURE: diagnostics do not describe a real operational forecast.",
  },
  "No forecast records are available for analysis.": {
    ru: "Для анализа нет записей прогноза.",
    kk: "Талдауға арналған болжам жазбалары жоқ.",
    en: "No forecast records are available for analysis.",
  },
};
