function formatReportSheet() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const sheet = ss.getActiveSheet();
  const lastRow = sheet.getLastRow();
  const lastCol = 5;
  if (lastRow < 1) return;

  // Убираем фильтр (стрелочки в шапке).
  // ВАЖНО: выполняй запуск после импорта/вставки данных, чтобы обновить оформление на активном листе.
  try {
    const filter = sheet.getFilter();
    if (filter) filter.remove();
  } catch (e) {}

  // Зафиксировать шапку
  sheet.setFrozenRows(1);

  // Оформление шапки A1:E1
  const headerRange = sheet.getRange(1, 1, 1, lastCol);
  const headerColors = ['#1D4ED8', '#059669', '#7C3AED', '#DC2626', '#F59E0B']; // яркая шапка по колонкам
  headerRange
    .setFontColor('#FFFFFF')
    .setFontWeight('bold')
    .setFontFamily('Arial')
    .setFontSize(12)
    .setHorizontalAlignment('center')
    .setVerticalAlignment('middle')
    .setWrap(true);

  // Удобная высота строки шапки
  sheet.setRowHeight(1, 38);

  // Красим каждую ячейку шапки своим цветом
  for (let c = 1; c <= lastCol; c++) {
    sheet.getRange(1, c).setBackground(headerColors[c - 1]);
  }

  // Размеры колонок под читаемость
  sheet.setColumnWidth(1, 170); // Никнейм
  sheet.setColumnWidth(2, 150); // Дата
  sheet.setColumnWidth(3, 230); // Время
  sheet.setColumnWidth(4, 240); // Всего наказаний
  sheet.setColumnWidth(5, 520); // Режимы

  // Выравнивание данных (со 2 строки)
  if (lastRow >= 2) {
    // Текст/выравнивание
    sheet.getRange(2, 1, lastRow - 1, 1).setHorizontalAlignment('left');   // ник
    sheet.getRange(2, 2, lastRow - 1, 1).setHorizontalAlignment('center'); // дата
    sheet.getRange(2, 3, lastRow - 1, 1).setHorizontalAlignment('left');   // время
    sheet.getRange(2, 4, lastRow - 1, 1).setHorizontalAlignment('right');  // кол-во наказаний
    sheet.getRange(2, 5, lastRow - 1, 1)
      .setHorizontalAlignment('left')
      .setVerticalAlignment('top')
      .setWrap(true); // режимы

    // Небольшой единый стиль шрифта для данных
    sheet.getRange(2, 1, lastRow - 1, lastCol).setFontFamily('Arial').setFontSize(10);

    // Чередование строк (полосы) - более ярко
    const stripeOdd = '#E0F2FE';   // светлый cyan
    const stripeEven = '#EEF2FF';  // светлый violet
    for (let r = 2; r <= lastRow; r++) {
      const bg = (r % 2 === 0) ? stripeEven : stripeOdd;
      sheet.getRange(r, 1, 1, lastCol).setBackground(bg);
    }

    // Колонка "Режимы" делаем отдельным акцентом, чтобы точно выглядело не тускло
    for (let r = 2; r <= lastRow; r++) {
      const bgE = (r % 2 === 0) ? '#FFF7ED' : '#ECFDF5'; // amber/pastel + green/pastel
      sheet.getRange(r, 5).setBackground(bgE);
    }

    // Чтобы “Общее количество наказаний” выделялось
    sheet.getRange(2, 4, lastRow - 1, 1).setFontWeight('bold');
  }

  // Бордеры по всем заполненным ячейкам (A1:ElastRow)
  const dataRange = sheet.getRange(1, 1, lastRow, lastCol);
  dataRange.setBorder(true, true, true, true, true, true, '#334155', SpreadsheetApp.BorderStyle.SOLID);

  // Более заметная нижняя линия под шапкой
  headerRange.setBorder(
    false,   // left
    false,   // right
    false,   // top
    true,    // bottom
    false,   // vertical
    false,   // horizontal
    '#0EA5E9',
    SpreadsheetApp.BorderStyle.SOLID
  );
}

