/** Evaluation copy preserves the scope, units and limitations of each metric. */
export const evaluationMessages: Record<string, { ru?: string; kk: string; en: string }> = {
  "Группа": {
    kk: "Топ",
    en: "Group",
  },
  "Прогнозов": {
    kk: "Болжам саны",
    en: "Forecasts",
  },
  "Качество модели": {
    kk: "Модель сапасы",
    en: "Model quality",
  },
  "Временная валидация · ошибка в normalized_power · меньше — лучше.": {
    kk: "Уақыт бойынша валидация · қате normalized_power бірлігінде · аз болғаны жақсы.",
    en: "Time-based validation · error in normalized_power · lower is better.",
  },
  "Выбранный кандидат:": {
    kk: "Таңдалған модель:",
    en: "Selected candidate:",
  },
  "TUNING · ВЫБОР МОДЕЛИ": {
    kk: "TUNING · МОДЕЛЬДІ ТАҢДАУ",
    en: "TUNING · MODEL SELECTION",
  },
  "MAE · равный вес турбин": {
    kk: "MAE · турбиналардың салмағы тең",
    en: "MAE · equal turbine weights",
  },
  "Кандидат выбран по этому периоду.": {
    kk: "Модель осы кезең бойынша таңдалды.",
    en: "The candidate was selected using this period.",
  },
  "НЕЗАВИСИМЫЙ HOLDOUT": {
    kk: "ТӘУЕЛСІЗ HOLDOUT",
    en: "INDEPENDENT HOLDOUT",
  },
  "WIND CURVE · HOLDOUT": {
    kk: "ЖЕЛ ҚИСЫҒЫ · HOLDOUT",
    en: "WIND CURVE · HOLDOUT",
  },
  "Простой baseline на том же периоде.": {
    kk: "Сол кезеңдегі қарапайым базалық модель.",
    en: "A simple baseline over the same period.",
  },
  "На holdout простой baseline лучше выбранной модели": {
    kk: "Holdout кезеңінде қарапайым базалық модель таңдалған модельден жақсы",
    en: "The simple baseline outperforms the selected model on holdout",
  },
  "Сравнение на отдельном holdout-периоде": {
    kk: "Бөлек holdout кезеңіндегі салыстыру",
    en: "Comparison on a separate holdout period",
  },
  "Ошибка wind curve ниже. Выбор модели сохранён по заранее заданному tuning-критерию; holdout не использовался для нового выбора.": {
    kk: "Жел қисығы моделінің қатесі төмен. Модель алдын ала белгіленген tuning критерийі бойынша таңдалған күйінде қалды; holdout қайта таңдау үшін қолданылған жоқ.",
    en: "The wind-curve error is lower. Model selection remains based on the predefined tuning criterion; holdout was not used to select a new model.",
  },
  "Сравнение относится к указанному периоду. Оно не устанавливает преимущество на будущих данных.": {
    kk: "Салыстыру көрсетілген кезеңге қатысты. Ол болашақ деректердегі басымдықты дәлелдемейді.",
    en: "This comparison applies to the stated period. It does not establish an advantage on future data.",
  },
  "Период выбора": {
    kk: "Таңдау кезеңі",
    en: "Selection period",
  },
  "Независимая оценка": {
    kk: "Тәуелсіз бағалау",
    en: "Independent evaluation",
  },
  "после начала → ": {
    kk: "басталу сәтінен кейін → ",
    en: "after the start → ",
  },
  "от начала → ": {
    kk: "басталу сәтінен → ",
    en: "from the start → ",
  },
  " включительно": {
    kk: " қоса алғанда",
    en: " inclusive",
  },
  "Граница выбора": {
    kk: "Таңдау шегі",
    en: "Selection cutoff",
  },
  "Cutoff модели": {
    kk: "Модельдің оқыту шегі",
    en: "Model training cutoff",
  },
  "Исходный часовой пояс отчёта:": {
    kk: "Есептің бастапқы уақыт белдеуі:",
    en: "Original report time zone:",
  },
  "Границы выше показаны в UTC. Перекрывающиеся прогнозы не являются независимыми наблюдениями.": {
    kk: "Жоғарыдағы шектер UTC бойынша көрсетілген. Уақыттары қабаттасатын болжамдар тәуелсіз бақылаулар болып саналмайды.",
    en: "The boundaries above are shown in UTC. Overlapping forecasts are not independent observations.",
  },
  "Production refit — отдельный пакет": {
    kk: "Production refit — бөлек пакет",
    en: "Production refit is a separate package",
  },
  "Обучение до {cutoff}.": {
    kk: "Оқыту шегі: {cutoff}.",
    en: "Training cutoff: {cutoff}.",
  },
  "В обучение включён holdout.": {
    kk: "Оқытуға holdout деректері енгізілген.",
    en: "Holdout is included in training.",
  },
  "Holdout не включён в обучение.": {
    kk: "Holdout деректері оқытуға енгізілмеген.",
    en: "Holdout is not included in training.",
  },
  "Независимая оценка итогового production refit не предоставлена.": {
    kk: "Соңғы production refit моделінің тәуелсіз бағалауы берілмеген.",
    en: "No independent evaluation of the final production refit is available.",
  },
  "Показанные holdout-метрики относятся к модели, замороженной до holdout, с cutoff {cutoff}.": {
    kk: "Көрсетілген holdout метрикалары holdout басталғанға дейін бекітілген, оқыту шегі {cutoff} болатын модельге қатысты.",
    en: "The displayed holdout metrics apply to the model frozen before holdout, with a training cutoff of {cutoff}.",
  },
  "Holdout по турбинам": {
    kk: "Турбиналар бойынша holdout",
    en: "Holdout by turbine",
  },
  "Holdout по горизонту, ч": {
    kk: "Болжам мерзімі бойынша holdout, сағ",
    en: "Holdout by horizon, h",
  },
  "Ограничения отчёта ({count})": {
    kk: "Есептің шектеулері ({count})",
    en: "Report limitations ({count})",
  },
  "Демо показывает интерфейс": {
    kk: "Демо интерфейсті көрсетеді",
    en: "Demo previews the interface",
  },
  "Отчёт оценки не прошёл проверку": {
    kk: "Бағалау есебі тексеруден өтпеді",
    en: "The evaluation report failed validation",
  },
  "Отчёт ещё не предоставлен": {
    kk: "Есеп әлі берілмеген",
    en: "No report has been provided yet",
  },
  "Синтетические значения не измеряют точность модели. Реальные метрики появятся после подключения проверенного отчёта.": {
    kk: "Жасанды мәндер модель дәлдігін өлшемейді. Нақты метрикалар тексерілген есеп қосылғаннан кейін көрсетіледі.",
    en: "Synthetic values do not measure model accuracy. Real metrics will appear when a validated report is connected.",
  },
  "Для показа метрик требуется отчёт, связанный с production-пакетом текущего прогноза.": {
    kk: "Метрикаларды көрсету үшін ағымдағы болжамның production пакетімен байланыстырылған есеп қажет.",
    en: "Displaying metrics requires a report linked to the production package of the current forecast.",
  },
  "Метрики недоступны": {
    kk: "Метрикалар қолжетімсіз",
    en: "Metrics unavailable",
  },
  "Доступность тестовых наблюдений указана сервисом; период и единицы сверяйте с отчётом.": {
    kk: "Тестілік бақылаулардың қолжетімділігін сервис көрсетеді; кезең мен өлшем бірліктерін есептен тексеріңіз.",
    en: "Test observation availability is reported by the service; check the report for the period and units.",
  },
  "Фактическая выработка февраля 2026 не предоставлена. Эти метрики не являются февральской точностью.": {
    kk: "2026 жылғы ақпанның нақты өндіріс деректері берілмеген. Бұл метрикалар ақпандағы дәлдікті көрсетпейді.",
    en: "Actual generation for February 2026 has not been provided. These metrics do not measure February accuracy.",
  },
  "MAE/RMSE не выражают процент точности или MW/MWh.": {
    kk: "MAE/RMSE дәлдік пайызын немесе MW/MWh шамасын білдірмейді.",
    en: "MAE/RMSE do not represent percentage accuracy or MW/MWh.",
  },
  "Полный отчёт и контрольные суммы": {
    kk: "Толық есеп және бақылау қосындылары",
    en: "Full report and checksums",
  },
  "Почасовой прогноз выбранных турбин. Точные значения приведены в таблице ниже.": {
    kk: "Таңдалған турбиналардың сағаттық болжамы. Нақты мәндер төмендегі кестеде берілген.",
    en: "Hourly forecast for the selected turbines. Exact values are provided in the table below.",
  },
};
