import sys
from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QPalette, QColor
from ui.main_window import MainWindow


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("CutFlow")

    # 시스템 테마(다크모드 등)에 무관하게 텍스트를 항상 검게 고정
    palette = app.palette()
    palette.setColor(QPalette.ColorRole.WindowText,   QColor('#111111'))
    palette.setColor(QPalette.ColorRole.Text,          QColor('#111111'))
    palette.setColor(QPalette.ColorRole.ButtonText,    QColor('#111111'))
    palette.setColor(QPalette.ColorRole.BrightText,    QColor('#111111'))
    palette.setColor(QPalette.ColorRole.ToolTipText,   QColor('#111111'))
    palette.setColor(QPalette.ColorRole.Window,        QColor('#f5f5f5'))
    palette.setColor(QPalette.ColorRole.Base,          QColor('#ffffff'))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor('#f0f0f0'))
    app.setPalette(palette)

    app.setStyleSheet("""
        QWidget          { color: #111111; }
        QLabel           { color: #111111; background: transparent; }
        QCheckBox        { color: #111111; }
        QRadioButton     { color: #111111; }
        QGroupBox        { color: #111111; }
        QComboBox        { color: #111111; background: white; }
        QComboBox QAbstractItemView { color: #111111; background: white; }
        QListWidget      { color: #111111; background: white; }
        QListWidget::item            { color: #111111; }
        QListWidget::item:selected   { color: #111111; background: #dde8fd; }
        QLineEdit        { color: #111111; background: white; }
        QTextEdit        { color: #111111; background: white; }
        QProgressBar     { color: #111111; text-align: center; }
        QToolTip         { color: #111111; background: #fffde7; border: 1px solid #bbb; }
        QScrollBar:vertical   { background: #eeeeee; width: 8px; }
        QScrollBar::handle:vertical { background: #bbbbbb; border-radius: 4px; }
    """)

    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
