export const appMessages: Record<
  string,
  { ru?: string; kk: string; en: string }
> = {
  "В очереди": {
    kk: "Кезекте",
    en: "Queued",
  },
  "Получение погоды": {
    kk: "Ауа райы деректерін алу",
    en: "Fetching weather",
  },
  "Проверка данных": {
    kk: "Деректерді тексеру",
    en: "Validating data",
  },
  Прогнозирование: {
    kk: "Болжам жасау",
    en: "Forecasting",
  },
  "Анализ результата": {
    kk: "Нәтижені талдау",
    en: "Analysing results",
  },
  "Расчёт завершён": {
    kk: "Есептеу аяқталды",
    en: "Calculation complete",
  },
  "Ошибка расчёта": {
    kk: "Есептеу қатесі",
    en: "Calculation failed",
  },
  "Не предоставлено": {
    kk: "Берілмеген",
    en: "Not provided",
  },
  "JelAI — обзор": {
    kk: "JelAI — шолу",
    en: "JelAI — overview",
  },
  Готово: {
    kk: "Дайын",
    en: "Done",
  },
  Сейчас: {
    kk: "Қазір",
    en: "In progress",
  },
  "Откуда берётся прогноз": {
    kk: "Болжам деректерінің көзі",
    en: "Where the forecast comes from",
  },
  "Версия модели, погодный выпуск и границы доступных данных.": {
    kk: "Модель нұсқасы, ауа райы болжамының шығарылымы және қолжетімді деректердің шектері.",
    en: "Model version, weather forecast issue and limits of available data.",
  },
  "Источник погоды": {
    kk: "Ауа райы деректерінің көзі",
    en: "Weather provider",
  },
  "Погодная модель": {
    kk: "Ауа райы моделі",
    en: "Weather model",
  },
  "Выпуск погоды": {
    kk: "Ауа райы болжамының шығарылымы",
    en: "Weather issue time",
  },
  "Доступен с": {
    kk: "Қолжетімді болған уақыт",
    en: "Available from",
  },
  "Основание доступности": {
    kk: "Қолжетімділік негіздемесі",
    en: "Availability basis",
  },
  "Модель прогноза": {
    kk: "Болжам моделі",
    en: "Forecast model",
  },
  "Обучение до": {
    kk: "Оқыту деректерінің соңғы уақыты",
    en: "Training cutoff",
  },
  "Контрольная сумма и метаданные": {
    kk: "Бақылау сомасы және метадеректер",
    en: "Checksum and metadata",
  },
  "Историческая доступность проверяется по исходным артефактам сервиса. Наличие метаданных на экране само по себе её не подтверждает.":
    {
      kk: "Деректердің сол сәтте қолжетімді болғаны сервистің бастапқы файлдары арқылы тексеріледі. Экрандағы метадеректер мұны өздігінен растамайды.",
      en: "Historical availability is verified against the service’s original artifacts. Metadata on screen does not by itself confirm it.",
    },
  "Полный журнал агента": {
    kk: "Агенттің толық журналы",
    en: "Full agent log",
  },
  "Демо-значение": {
    kk: "Демо мән",
    en: "Demo value",
  },
  "Нормализованная мощность": {
    kk: "Нормаланған қуат",
    en: "Normalised power",
  },
  "Нужна проверка": {
    kk: "Тексеру қажет",
    en: "Needs review",
  },
  "Ожидание выпуска": {
    kk: "Шығарылымды күту",
    en: "Awaiting an issue",
  },
  "Подготовка демо": {
    kk: "Демоны дайындау",
    en: "Preparing demo",
  },
  "Выберите исторический выпуск с 31 января по 28 февраля 2026.": {
    kk: "2026 жылғы 31 қаңтар мен 28 ақпан аралығындағы тарихи шығарылымды таңдаңыз.",
    en: "Select a historical issue between 31 January and 28 February 2026.",
  },
  "К данным прогноза": {
    kk: "Болжам деректеріне өту",
    en: "Skip to forecast data",
  },
  Навигация: {
    kk: "Навигация",
    en: "Navigation",
  },
  "JelAI — начало": {
    kk: "JelAI — басты бет",
    en: "JelAI — home",
  },
  Обзор: {
    kk: "Шолу",
    en: "Overview",
  },
  "Почасовой прогноз": {
    kk: "Сағаттық болжам",
    en: "Hourly forecast",
  },
  "Источник и качество": {
    kk: "Дереккөз және сапа",
    en: "Source and quality",
  },
  "Оценка модели": {
    kk: "Модельді бағалау",
    en: "Model evaluation",
  },
  "Подключение и бренд": {
    kk: "Қосылым және бренд",
    en: "Connection and brand",
  },
  "HackAlem AI · Энергетика": {
    kk: "HackAlem AI · Энергетика",
    en: "HackAlem AI · Energy",
  },
  "Рабочее пространство": {
    kk: "Жұмыс кеңістігі",
    en: "Workspace",
  },
  Ветропарк: {
    kk: "Жел электр станциясы",
    en: "Wind farm",
  },
  "Язык интерфейса": {
    kk: "Интерфейс тілі",
    en: "Interface language",
  },
  "Источник данных": {
    kk: "Деректер көзі",
    en: "Data source",
  },
  Демо: {
    kk: "Демо",
    en: "Demo",
  },
  "Настройки подключения": {
    kk: "Қосылым баптаулары",
    en: "Connection settings",
  },
  "ЭНЕРГЕТИКА · HACKALEM AI": {
    kk: "ЭНЕРГЕТИКА · HACKALEM AI",
    en: "ENERGY · HACKALEM AI",
  },
  "Обзор ветропарка": {
    kk: "Жел станциясына шолу",
    en: "Wind farm overview",
  },
  "Демонстрационный режим": {
    kk: "Демонстрациялық режим",
    en: "Demo mode",
  },
  "API · синтетическое демо": {
    kk: "API · синтетикалық демо",
    en: "API · synthetic demo",
  },
  "API подключён": {
    kk: "API қосылған",
    en: "API connected",
  },
  "Режим API": {
    kk: "API режимі",
    en: "API mode",
  },
  "Обзор выпуска": {
    kk: "Шығарылымға шолу",
    en: "Issue overview",
  },
  "JELAI / ПРОГНОЗ ВЕТРОЭНЕРГИИ": {
    kk: "JELAI / ЖЕЛ ЭНЕРГИЯСЫН БОЛЖАУ",
    en: "JELAI / WIND ENERGY FORECAST",
  },
  "Энергия ветра.": {
    kk: "Жел энергиясы.",
    en: "Wind energy.",
  },
  "Ясность на 48 часов.": {
    kk: "Алдағы 48 сағат анық.",
    en: "Clarity for the next 48 hours.",
  },
  "От погодного выпуска к почасовому прогнозу.": {
    kk: "Ауа райы деректерінен сағаттық болжамға дейін.",
    en: "From a weather issue to an hourly forecast.",
  },
  "Условное значение · среднее": {
    kk: "Шартты мән · орташа",
    en: "Demo value · mean",
  },
  "Норм. мощность · среднее": {
    kk: "Норм. қуат · орташа",
    en: "Norm. power · mean",
  },
  "Синтетические данные · не реальный прогноз": {
    kk: "Синтетикалық деректер · нақты болжам емес",
    en: "Synthetic data · not a real forecast",
  },
  "Исторический расчёт · не live-телеметрия": {
    kk: "Тарихи есептеу · жедел телеметрия емес",
    en: "Historical calculation · not live telemetry",
  },
  "AI-агент": {
    kk: "AI агенті",
    en: "AI agent",
  },
  "В работе": {
    kk: "Орындалуда",
    en: "Running",
  },
  Ожидание: {
    kk: "Күту",
    en: "Waiting",
  },
  "Горизонт выпуска": {
    kk: "Шығарылымның болжам аралығы",
    en: "Issue horizon",
  },
  "{hours} ч": {
    kk: "{hours} сағ",
    en: "{hours} h",
  },
  "Обновить статус": {
    kk: "Күйді жаңарту",
    en: "Refresh status",
  },
  "Иллюстрация ветропарка": {
    kk: "Жел станциясының иллюстрациясы",
    en: "Wind farm illustration",
  },
  "Параметры выпуска": {
    kk: "Шығарылым параметрлері",
    en: "Issue parameters",
  },
  "Дата выпуска": {
    kk: "Шығарылым күні",
    en: "Issue date",
  },
  "Время, UTC": {
    kk: "Уақыт, UTC",
    en: "Time, UTC",
  },
  "Время выпуска UTC": {
    kk: "Шығарылым уақыты, UTC",
    en: "Issue time UTC",
  },
  Турбины: {
    kk: "Турбиналар",
    en: "Turbines",
  },
  "Обе турбины": {
    kk: "Екі турбина",
    en: "Both turbines",
  },
  "Турбина 01": {
    kk: "Турбина 01",
    en: "Turbine 01",
  },
  "Турбина 02": {
    kk: "Турбина 02",
    en: "Turbine 02",
  },
  Горизонт: {
    kk: "Болжам аралығы",
    en: "Horizon",
  },
  "Горизонт прогноза": {
    kk: "Болжам аралығы",
    en: "Forecast horizon",
  },
  "Расчёт…": {
    kk: "Есептелуде…",
    en: "Calculating…",
  },
  "Рассчитать прогноз": {
    kk: "Болжамды есептеу",
    en: "Calculate forecast",
  },
  "Проверьте параметры": {
    kk: "Параметрлерді тексеріңіз",
    en: "Check the parameters",
  },
  "Результат не подтверждён": {
    kk: "Нәтиже расталмады",
    en: "Result not confirmed",
  },
  "Автоматический опрос приостановлен. Проверьте подключение и обновите статус.":
    {
      kk: "Автоматты сұрау тоқтатылды. Қосылымды тексеріп, күйді жаңартыңыз.",
      en: "Automatic polling is paused. Check the connection and refresh the status.",
    },
  "Повторить запрос": {
    kk: "Сұрауды қайталау",
    en: "Retry request",
  },
  "Данные прогноза": {
    kk: "Болжам деректері",
    en: "Forecast data",
  },
  Прогноз: {
    kk: "Болжам",
    en: "Forecast",
  },
  "Часовой пояс отображения": {
    kk: "Көрсетілетін уақыт белдеуі",
    en: "Display time zone",
  },
  "Алматы · UTC+5": {
    kk: "Алматы · UTC+5",
    en: "Almaty · UTC+5",
  },
  "Выпуск {date}": {
    kk: "Шығарылым: {date}",
    en: "Issue {date}",
  },
  "ID {id} · рев. {revision}": {
    kk: "ID {id} · түзету {revision}",
    en: "ID {id} · rev. {revision}",
  },
  "{quantity} · среднее за час": {
    kk: "{quantity} · сағаттық орташа",
    en: "{quantity} · hourly mean",
  },
  "Подготовка графика": {
    kk: "Графикті дайындау",
    en: "Preparing chart",
  },
  "Время обозначает конец часа. Фактические наблюдения не представлены.": {
    kk: "Уақыт сағаттық аралықтың соңын білдіреді. Нақты бақылау деректері берілмеген.",
    en: "Time marks the end of the hour. Actual observations are not provided.",
  },
  "Полнота прогноза": {
    kk: "Болжамның толықтығы",
    en: "Forecast completeness",
  },
  "часовой сетки": {
    kk: "сағаттық тордың",
    en: "of the hourly grid",
  },
  "Получено значений": {
    kk: "Алынған мәндер",
    en: "Values received",
  },
  "Пропущено часов": {
    kk: "Жетіспейтін сағаттар",
    en: "Missing hours",
  },
  "Полнота данных, не точность модели.": {
    kk: "Деректердің толықтығы, модельдің дәлдігі емес.",
    en: "Data completeness, not model accuracy.",
  },
  "ПОГОДНЫЙ ИСТОЧНИК": {
    kk: "АУА РАЙЫ ДЕРЕККӨЗІ",
    en: "WEATHER SOURCE",
  },
  "Выпуск:": {
    kk: "Шығарылым:",
    en: "Issue:",
  },
  "не указан": {
    kk: "көрсетілмеген",
    en: "not specified",
  },
  "Подробнее об источнике": {
    kk: "Дереккөз туралы толығырақ",
    en: "More about the source",
  },
  ПРОВЕРЯЕМОСТЬ: {
    kk: "ТЕКСЕРУ МҮМКІНДІГІ",
    en: "VERIFIABILITY",
  },
  "Синтетический пример": {
    kk: "Синтетикалық мысал",
    en: "Synthetic example",
  },
  "Метаданные выпуска": {
    kk: "Шығарылым метадеректері",
    en: "Issue metadata",
  },
  "Точность модели не измеряется": {
    kk: "Модель дәлдігі өлшенбейді",
    en: "Model accuracy is not measured",
  },
  "Источник, версия модели и cutoff": {
    kk: "Дереккөз, модель нұсқасы және оқыту шегі",
    en: "Source, model version and training cutoff",
  },
  "Открыть оценку модели": {
    kk: "Модель бағасын ашу",
    en: "Open model evaluation",
  },
  "Почасовые значения": {
    kk: "Сағаттық мәндер",
    en: "Hourly values",
  },
  "Результат API": {
    kk: "API нәтижесі",
    en: "API result",
  },
  "Скачать CSV": {
    kk: "CSV жүктеп алу",
    en: "Download CSV",
  },
  "CSV недоступен: {error}": {
    kk: "CSV қолжетімсіз: {error}",
    en: "CSV unavailable: {error}",
  },
  "Конец часового интервала": {
    kk: "Сағаттық аралықтың соңы",
    en: "End of hourly interval",
  },
  Турбина: {
    kk: "Турбина",
    en: "Turbine",
  },
  Источник: {
    kk: "Дереккөз",
    en: "Source",
  },
  "+{hours} ч": {
    kk: "+{hours} сағ",
    en: "+{hours} h",
  },
  Синтетический: {
    kk: "Синтетикалық",
    en: "Synthetic",
  },
  "Значения турбин не суммируются и не переводятся в МВт без подтверждённого масштаба.":
    {
      kk: "Масштаб расталмайынша, турбина мәндері қосылмайды және МВт-қа аударылмайды.",
      en: "Turbine values are not summed or converted to MW without a confirmed scale.",
    },
  "CSV сверяется с результатом": {
    kk: "CSV нәтижемен салыстырылады",
    en: "CSV checked against the result",
  },
  "Прогноз пока недоступен": {
    kk: "Болжам әзірге қолжетімсіз",
    en: "Forecast not yet available",
  },
  "Начните с исторического выпуска": {
    kk: "Тарихи шығарылымнан бастаңыз",
    en: "Start with a historical issue",
  },
  "Агент проверяет входные данные и готовит результат. Состояние обновляется автоматически.":
    {
      kk: "Агент кіріс деректерін тексеріп, нәтижені дайындауда. Күй автоматты түрде жаңарады.",
      en: "The agent is validating the input and preparing the result. The status updates automatically.",
    },
  "Выберите дату, турбины и горизонт, затем нажмите «Рассчитать прогноз».": {
    kk: "Күнді, турбиналарды және болжам аралығын таңдап, «Болжамды есептеу» түймесін басыңыз.",
    en: "Select the date, turbines and horizon, then click “Calculate forecast”.",
  },
  "Проверить подключение": {
    kk: "Қосылымды тексеру",
    en: "Check connection",
  },
  "Журнал агента": {
    kk: "Агент журналы",
    en: "Agent log",
  },
  "История запусков": {
    kk: "Іске қосу тарихы",
    en: "Run history",
  },
  "Текущий расчёт": {
    kk: "Ағымдағы есептеу",
    en: "Current calculation",
  },
  "Энергия данных. Сила ветра.": {
    kk: "Дерек қуаты. Жел күші.",
    en: "The energy of data. The power of wind.",
  },
  "HackAlem AI 2026 · Трек «Энергетика»": {
    kk: "HackAlem AI 2026 · «Энергетика» бағыты",
    en: "HackAlem AI 2026 · Energy track",
  },
  "Закрыть настройки": {
    kk: "Баптауларды жабу",
    en: "Close settings",
  },
  "Режим API использует погодный сервис команды. Демо работает самостоятельно.":
    {
      kk: "API режимі команданың ауа райы сервисін пайдаланады. Демо өздігінен жұмыс істейді.",
      en: "API mode uses the team’s weather service. Demo mode works independently.",
    },
  "Адрес API": {
    kk: "API мекенжайы",
    en: "API address",
  },
  "Локальный путь /api подключён к серверу команды. Другой адрес должен разрешать запросы из браузера.":
    {
      kk: "Жергілікті /api жолы команда серверіне қосылған. Басқа мекенжай браузер сұрауларына рұқсат беруі керек.",
      en: "The local /api path connects to the team’s server. A different address must allow browser requests.",
    },
  "Применить адрес": {
    kk: "Мекенжайды қолдану",
    en: "Apply address",
  },
  Проверить: {
    kk: "Тексеру",
    en: "Check",
  },
  "API отвечает · синтетическое демо": {
    kk: "API жауап берді · синтетикалық демо",
    en: "API responding · synthetic demo",
  },
  "API отвечает": {
    kk: "API жауап берді",
    en: "API responding",
  },
  "Готовность сервиса": {
    kk: "Сервистің дайындығы",
    en: "Service readiness",
  },
  "«Жел» + AI. Три потока вокруг центра — ветер, данные и прогноз.": {
    kk: "«Жел» + AI. Ортаны айнала орналасқан үш ағын — жел, деректер және болжам.",
    en: "“Jel” (wind in Kazakh) + AI. Three flows around the centre represent wind, data and forecasts.",
  },
  "Скачать логотип SVG": {
    kk: "SVG логотипін жүктеп алу",
    en: "Download SVG logo",
  },
  "Скачать знак": {
    kk: "Белгіні жүктеп алу",
    en: "Download brand mark",
  },
};
