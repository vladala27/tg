import logging
import sqlite3
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from datetime import datetime, timedelta
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

# Настройка логирования
import logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


# Токен вашего бота
BOT_TOKEN = '7633388264:AAG890LBISxHwxXjKM__IaX5aqa_lBa9Cqk'

# Инициализация бота и диспетчера
bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# Подключение к базе данных
conn = sqlite3.connect('tasks.db', check_same_thread=False)
cursor = conn.cursor()

# Создание таблиц
cursor.execute('''
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY
)
''')

cursor.execute('''
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    category TEXT,
    task TEXT,
    is_completed INTEGER DEFAULT 0,
    FOREIGN KEY (user_id) REFERENCES users (user_id)
)
''')

# После создания таблиц добавим проверку столбца is_completed
cursor.execute('PRAGMA table_info(tasks)')
columns = [column[1] for column in cursor.fetchall()]
if 'is_completed' not in columns:
    cursor.execute('ALTER TABLE tasks ADD COLUMN is_completed INTEGER DEFAULT 0')
    conn.commit()

# Состояния для FSM (Finite State Machine)
class TaskStates(StatesGroup):
    waiting_for_category = State()
    waiting_for_task = State()
    waiting_for_category_to_delete = State()
    waiting_for_task_to_delete = State()
    waiting_for_category_to_change = State()
    waiting_for_task_to_change_category = State()
    waiting_for_new_category = State()
    waiting_for_task_to_select = State()
    waiting_for_task_to_mark_complete = State()
    waiting_for_category_to_mark_complete = State()
    waiting_for_task_to_mark_complete = State()
    waiting_for_edit_note = State()
    waiting_for_edit_choice = State()
    waiting_for_new_title = State()
    waiting_for_new_text = State()

# Функция для создания клавиатуры
def create_keyboard(buttons):
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=btn)] for btn in buttons],
        resize_keyboard=True
    )

# Клавиатура для подтверждения удаления категории
def confirm_category_delete_keyboard(category):
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Да", callback_data=f"confirm_delete_cat_{category}"),
            InlineKeyboardButton(text="❌ Нет", callback_data="cancel_delete_cat")
        ]
    ])

async def check_state_timeout(state: FSMContext):
    data = await state.get_data()
    if 'last_activity' in data:
        last_activity = datetime.fromisoformat(data['last_activity'])
        if datetime.now() - last_activity > timedelta(minutes=5):
            await state.clear()
            return True
    return False
# Функция для создания inline-клавиатуры с заметками
def notes_inline_keyboard(notes):
    keyboard = []
    for note in notes:
        keyboard.append([InlineKeyboardButton(text=note[0], callback_data=f"note_{note[0]}")])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

# Функция для клавиатуры выбора действия
def edit_choice_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Название", callback_data="edit_title"),
         InlineKeyboardButton(text="📝 Текст", callback_data="edit_text")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_edit")]
    ])

    # Обработчик отмены изменения
@dp.callback_query(TaskStates.waiting_for_edit_choice, lambda c: c.data == "cancel_edit")
async def cancel_edit(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.answer("Изменение заметки отменено.")
    await state.clear()
    await callback.answer()

# Команда /start
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    cursor.execute('INSERT OR IGNORE INTO users (user_id) VALUES (?)', (user_id,))
    conn.commit()
    keyboard = create_keyboard([
        "Добавить заметку",
        "Показать заметки",
        "Удалить заметку",
        "Изменить заметку",
        "Перезапустить"
    ])
    await message.answer(
        "👋 Привет! Я твой персональный бот!\n"
        "Выбери действие из меню ниже:",
        reply_markup=keyboard,
        parse_mode='HTML'
    )

@dp.message(lambda message: message.text == "Перезапустить")
async def restart_bot(message: types.Message):
    await cmd_start(message)
    
async def get_category(message: types.Message, state: FSMContext, next_state: State):
    if await check_state_timeout(state):
        await message.answer("⏳ Сессия истекла, начните заново.")
        return
    
    await state.update_data(last_activity=datetime.now().isoformat())

    user_id = message.from_user.id
    cursor.execute('SELECT DISTINCT category FROM tasks WHERE user_id = ?', (user_id,))
    categories = cursor.fetchall()
    
    if categories:
        categories_list = "\n".join([f"{i + 1}. {category[0]}" for i, category in enumerate(categories)])
        await message.answer(f"Выберите категорию:\n{categories_list}")
        await state.set_state(next_state)
        await state.update_data(user_id=user_id)
    else:
        await message.answer("Задач пока нет.")
        await state.clear()
        
async def get_task(message: types.Message, state: FSMContext, next_state: State):
    try:
        logger.info(f"Entered get_task with state: {await state.get_state()}")

        # Проверяем активное состояние
        current_state = await state.get_state()
        if current_state is None:
            await message.answer("⚠️ Сессия устарела, начните операцию заново.")
            return

        # Получаем user_id из состояния
        data = await state.get_data()
        user_id = data.get("user_id")
        if not user_id:
            logger.error("User ID not found in state")
            await state.clear()
            await message.answer("❌ Ошибка сессии, начните заново.")
            return

        # Проверяем и преобразуем ввод пользователя
        try:
            category_number = int(message.text.strip()) - 1
        except ValueError:
            logger.warning("Invalid input - not a number")
            await message.answer("❌ Пожалуйста, введите <b>число</b> из списка.", parse_mode='HTML')
            await state.clear()
            return

        # Получаем список категорий
        cursor.execute('SELECT DISTINCT category FROM tasks WHERE user_id = ?', (user_id,))
        categories = [row[0] for row in cursor.fetchall()]
        
        if not categories:
            logger.warning("No categories found")
            await message.answer("❌ Категории не найдены.")
            await state.clear()
            return

        # Проверяем диапазон введенного номера
        if not (0 <= category_number < len(categories)):
            logger.warning(f"Invalid category number: {category_number + 1} (max {len(categories)})")
            await message.answer(f"❌ Неверный номер категории. Введите число от 1 до {len(categories)}")
            await state.clear()
            return

        category = categories[category_number]
        logger.info(f"Selected category: {category}")

        # Получаем задачи для категории
        cursor.execute('SELECT id, task FROM tasks WHERE user_id = ? AND category = ?', (user_id, category))
        task_list = cursor.fetchall()
        
        if not task_list:
            logger.warning(f"No tasks in category: {category}")
            await message.answer(f"📭 В категории <b>'{category}'</b> задач нет.", parse_mode='HTML')
            await state.clear()
            return

        # Формируем список задач
        tasks_list = "\n".join([f"{i + 1}. {task[1]}" for i, task in enumerate(task_list)])
        await message.answer(f"Выберите номер задачи:\n{tasks_list}")
        
        # Обновляем состояние
        await state.update_data(
            category=category,
            task_list=task_list,
            last_action=datetime.now().isoformat()
        )
        await state.set_state(next_state)
        logger.info(f"State updated to {next_state}")

    except sqlite3.Error as e:
        logger.error(f"Database error: {str(e)}")
        await message.answer("⚠️ Ошибка базы данных. Попробуйте позже.")
        await state.clear()
        
    except Exception as e:
        logger.error(f"Unexpected error: {str(e)}", exc_info=True)
        await message.answer("⚠️ Непредвиденная ошибка. Начните заново.")
        await state.clear()

# Обработка кнопки "Добавить заметку"
@dp.message(lambda message: message.text == "Добавить заметку")
async def add_task(message: types.Message, state: FSMContext):
    await message.answer("Введите название для заметки:")
    await state.set_state(TaskStates.waiting_for_category)
    await state.update_data(user_id=message.from_user.id)

# Обработка ввода категории
@dp.message(TaskStates.waiting_for_category)
async def process_category(message: types.Message, state: FSMContext):
    user_id = (await state.get_data()).get("user_id")
    category = message.text.strip()
    
    # Проверяем, существует ли такая категория для данного пользователя
    cursor.execute('SELECT DISTINCT category FROM tasks WHERE user_id = ?', (user_id,))
    categories = [row[0] for row in cursor.fetchall()]
    
    if category in categories:
        # Если категория уже существует, просим ввести новое название
        await message.answer(f"❌ Заметка с названием <b>'{category}'</b> уже существует. Пожалуйста, введите другое название:",
        parse_mode='HTML')
        # Остаемся в том же состоянии, чтобы пользователь мог ввести новое название
        return
    
    # Если категории нет, сообщаем, что она будет создана
    await message.answer(f"🎉 Создана новая заметка <b>'{category}'</b>! Теперь введите текст заметки:",
    parse_mode='HTML')
    
    # Обновляем состояние и переходим к вводу задачи
    await state.update_data(category=category)
    await state.set_state(TaskStates.waiting_for_task)

# Обработка ввода задачи
@dp.message(TaskStates.waiting_for_task)
async def process_task(message: types.Message, state: FSMContext):
    user_id = (await state.get_data()).get("user_id")
    data = await state.get_data()
    category = data.get("category")
    task = message.text.strip()
    
    # Проверяем, существует ли такая категория для данного пользователя
    cursor.execute('SELECT DISTINCT category FROM tasks WHERE user_id = ?', (user_id,))
    categories = [row[0] for row in cursor.fetchall()]
    
    if category not in categories:
        # Если категории нет, создаем её
        await message.answer(f"Создана новая заметка '{category}'.")
    
    # Добавляем задачу в категорию
    cursor.execute('INSERT INTO tasks (user_id, category, task) VALUES (?, ?, ?)', (user_id, category, task))
    conn.commit()
    await message.answer(f"✅ Текст успешно добавлен в заметку <b>'{category}'</b>:\n"
        f"▫️ <i>{task}</i>",
        parse_mode='HTML')
    await state.clear()

# Обработка кнопки "Показать заметки"
@dp.message(lambda message: message.text == "Показать заметки")
async def show_tasks(message: types.Message):
    user_id = message.from_user.id
    cursor.execute('SELECT category, task, is_completed FROM tasks WHERE user_id = ?', (user_id,))
    tasks = cursor.fetchall()
    
    if tasks:
        # Группируем задачи по категориям
        tasks_by_category = {}
        for category, task, is_completed in tasks:
            if category not in tasks_by_category:
                tasks_by_category[category] = []
            tasks_by_category[category].append(task)  
        
        # Формируем ответ
        response = "📂 Ваши заметки:\n\n"
        for category, tasks_list in tasks_by_category.items():
            response += f"📁 <b>{category}</b>\n"
            for task in tasks_list:
                response += f"  └ {task}\n" 
            response += "\n"
        
        await message.answer(response, parse_mode='HTML')
    else:
        await message.answer("Заметок пока нет.")

@dp.errors()
async def errors_handler(update: types.Update, exception: Exception):
    logger.error(f"Global error: {exception}")
    return True

# Обработчик кнопки "Удалить заметку"
@dp.message(lambda message: message.text == "Удалить заметку")
async def delete_note_start(message: types.Message):
    user_id = message.from_user.id
    cursor.execute('SELECT DISTINCT category FROM tasks WHERE user_id = ?', (user_id,))
    categories = [row[0] for row in cursor.fetchall()]
    
    if not categories:
        await message.answer("Нет заметок для удаления.")
        return
    
    # Создаем inline-клавиатуру с категориями
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=cat, callback_data=f"delete_cat_{cat}")]
        for cat in categories
    ])
    
    await message.answer("Выберите заметку для удаления:", reply_markup=keyboard)

# Обработчик выбора категории для удаления
@dp.callback_query(lambda c: c.data.startswith("delete_cat_"))
async def process_delete_category(callback: types.CallbackQuery):
    category = callback.data.split("_", 2)[-1]
    
    await callback.message.edit_text(
        f"🗑 Вы действительно хотите удалить ВСЮ заметку <b>'{category}'</b>?\n"
        "Это действие нельзя отменить!",
        parse_mode="HTML",
        reply_markup=confirm_category_delete_keyboard(category)
    )
    await callback.answer()

# Обработчик подтверждения удаления категории
@dp.callback_query(lambda c: c.data.startswith("confirm_delete_cat_"))
async def process_confirm_delete_category(callback: types.CallbackQuery):
    category = callback.data.split("_", 3)[-1]
    user_id = callback.from_user.id
    
    # Удаляем все задачи в категории
    cursor.execute('DELETE FROM tasks WHERE user_id = ? AND category = ?', (user_id, category))
    conn.commit()
    
    await callback.message.edit_text(f"✅ Заметка <b>'{category}'</b> полностью удалена!", parse_mode="HTML")
    await callback.answer()

# Обработчик отмены удаления категории
@dp.callback_query(lambda c: c.data == "cancel_delete_cat")
async def process_cancel_delete_category(callback: types.CallbackQuery):
    await callback.message.edit_text("❌ Удаление заметки отменено.")
    await callback.answer()

# Обработчик кнопки "Изменить заметку"
@dp.message(lambda message: message.text == "Изменить заметку")
async def edit_note_start(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    cursor.execute('SELECT DISTINCT category FROM tasks WHERE user_id = ?', (user_id,))
    notes = cursor.fetchall()
    
    if not notes:
        await message.answer("У вас пока нет заметок для изменения.")
        return
    
    await message.answer("Выберите заметку для изменения:", 
                        reply_markup=notes_inline_keyboard(notes))
    await state.set_state(TaskStates.waiting_for_edit_note)

# Обработчик выбора заметки
@dp.callback_query(TaskStates.waiting_for_edit_note, lambda c: c.data.startswith("note_"))
async def process_note_selection(callback: types.CallbackQuery, state: FSMContext):
    note_title = callback.data.split("_")[1]
    await state.update_data(selected_note=note_title)
    await callback.message.answer(f"Выбрана заметка: {note_title}\nЧто вы хотите изменить?",
                                 reply_markup=edit_choice_keyboard())
    await state.set_state(TaskStates.waiting_for_edit_choice)
    await callback.answer()

# Обработчик выбора действия
@dp.callback_query(TaskStates.waiting_for_edit_choice)
async def process_edit_choice(callback: types.CallbackQuery, state: FSMContext):
    action = callback.data
    data = await state.get_data()
    note_title = data.get('selected_note')
    
    if action == "edit_title":
        await callback.message.answer("Введите новое название для заметки:")
        await state.set_state(TaskStates.waiting_for_new_title)
    elif action == "edit_text":
        await callback.message.answer("Введите новый текст для заметки:")
        await state.set_state(TaskStates.waiting_for_new_text)
    
    await callback.answer()

# Обработка изменения названия
@dp.message(TaskStates.waiting_for_new_title)
async def process_new_title(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    data = await state.get_data()
    old_title = data.get('selected_note')
    new_title = message.text.strip()
    
    # Обновляем все записи с старым названием
    cursor.execute('UPDATE tasks SET category = ? WHERE user_id = ? AND category = ?',
                  (new_title, user_id, old_title))
    conn.commit()
    
    await message.answer(f"✅ Заметка успешно переименована:\n{old_title} → {new_title}")
    await state.clear()

# Обработка изменения текста
@dp.message(TaskStates.waiting_for_new_text)
async def process_new_text(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    data = await state.get_data()
    note_title = data.get('selected_note')
    new_text = message.text.strip()
    
    # Обновляем все записи в выбранной категории
    cursor.execute('''UPDATE tasks SET task = ? 
                   WHERE user_id = ? AND category = ?''',
                  (new_text, user_id, note_title))
    conn.commit()
    
    await message.answer(f"✅ Текст заметки обновлен во всех записях:\n{new_text}")
    await state.clear()
     
@dp.message()
async def unhandled_message(message: types.Message):
    await message.answer(
        "⚠️ Не понимаю команду. Используйте кнопки меню.",
        reply_markup=create_keyboard([
            "Добавить задачу",
            "Показать задачи",
            "Перезапустить"
        ])
    )

# Запуск бота
async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
