import asyncio
import datetime
import logging
import os
import random
import time
import urllib3

import aiogram.utils.markdown as md
from aiogram import Bot, Dispatcher, types
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters import Text
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.types import (InlineKeyboardButton, InlineKeyboardMarkup, ParseMode, ReplyKeyboardMarkup, ReplyKeyboardRemove)
from aiogram.utils import executor
from outline_vpn.outline_vpn import OutlineVPN

from yoomoney import Client, Quickpay

import config

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# настройка логирования
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

bot = Bot(token=config.telegram_token)
storage = MemoryStorage()
dp = Dispatcher(bot, storage=storage)

try:
    outline_client = OutlineVPN(api_url=config.outline_api_url, cert_sha256=config.outline_cert_sha256)
    yoomoney_client = Client(config.yoomoney_token)
except Exception as e:
    logger.critical(f"Не удалось инициализировать клиенты API: {e}")

class PromoCodeState(StatesGroup):
    waiting_for_code = State()

class SupportState(StatesGroup):
    waiting_for_message = State()
    admin_response = State()

class MailingState(StatesGroup):
    waiting_for_text = State()


def ensure_dirs_exist(): # Создаем все необходимые директории, если их нет
    dirs_to_check = [
        'data',
        'data/promocodes',
        'data/promocodes/discounts',
        'data/transaction_logs',
        'data/texts',
        'data/notifications',
        'attachments'
    ]
    for directory in dirs_to_check:
        if not os.path.exists(directory):
            os.makedirs(directory)
            logger.info(f"Создана директория: {directory}")

def read_file(path, default=""):
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return f.read().strip()
    except FileNotFoundError:
        logger.warning(f"Файл не найден: {path}. Возвращено значение по умолчанию.")
        return default

def read_file_lines(path):
    content = read_file(path)
    return content.splitlines() if content else []

async def append_to_file(path, content):
    try:
        with open(path, 'a', encoding='utf-8') as f:
            f.write(str(content) + '\n')
    except IOError as e:
        logger.error(f"Ошибка записи в файл {path}: {e}")

async def get_moscow_time():
    return datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=3)))


### РАБОТА С OUTLINE VPN ###

async def create_outline_key(user_id: int, gb_limit: int = 0, name_prefix: str = "Paid"):
    try:
        new_key = outline_client.create_key()
        key_name = f"{name_prefix}_{user_id}_{int(time.time())}"
        outline_client.rename_key(new_key.key_id, key_name)

        if gb_limit > 0 and gb_limit < 998:  # 998, 999 - коды для безлимита
            bytes_limit = gb_limit * 1024 * 1024 * 1024
            outline_client.add_data_limit(new_key.key_id, bytes_limit)

        await append_to_file(config.KEYS_IDS_FILE, f'{user_id}||{new_key.access_url}||{new_key.key_id}')

        # Установка срока действия ключа
        months_to_add = 3 if gb_limit == 999 else 1
        moscow_now = await get_moscow_time()
        expiration_date = moscow_now + datetime.timedelta(days=30 * months_to_add)
        await append_to_file(config.USERS_KEYS_EXPIRATIONS_FILE, f'{user_id}||{int(expiration_date.timestamp())}||{new_key.key_id}')

        logger.info(f"Создан ключ {new_key.key_id} для пользователя {user_id} с лимитом {gb_limit}GB")
        return new_key.access_url
    except Exception as e:
        logger.error(f"Ошибка при создании ключа Outline для {user_id}: {e}")
        return None


### РАБОТА С YOOMONEY ###

async def generate_payment(amount: float, user_id: int, description: str):
    """Генерирует объект платежа Quickpay."""
    label = f"{user_id}_{int(time.time())}_{random.randint(1000, 9999)}"
    quickpay = Quickpay(
        receiver=config.yoomoney_wallet,
        quickpay_form="shop",
        targets=description,
        paymentType="SB",
        sum=amount,
        label=label
    )
    return quickpay

async def check_yoomoney_payment(label: str, expected_amount: float):
    try:
        history = yoomoney_client.operation_history(label=label)
        for operation in history.operations:
            if operation.status == 'success' and float(operation.amount) >= expected_amount:
                logger.info(f"Успешная оплата найдена по {label} на сумму {operation.amount}")
                return True
    except Exception as e:
        logger.error(f"Ошибка при проверке платежа YooMoney по {label}: {e}")
    return False


### ТЕКСТЫ И КЛАВИАТУРЫ ###

start_text = read_file(config.START_MESSAGE_FILE, "Добро пожаловать!")
support_text = read_file(config.SUPPORT_MESSAGE_FILE, "Свяжитесь с поддержкой.")
info_text = read_file(config.INFORMATION_FILE, "Информация о сервисе.")
guide_text = read_file(config.GUIDE_FILE, "Инструкция по использованию.")

main_menu_kb = InlineKeyboardMarkup(row_width=2).add(
    InlineKeyboardButton('🛒 Купить VPN', callback_data='buy_vpn'),
    InlineKeyboardButton('📚 Информация', callback_data='info'),
    InlineKeyboardButton('💬 Поддержка', callback_data='support'),
    InlineKeyboardButton('🎁 Мои ключи', callback_data='my_keys'),
    InlineKeyboardButton('🔥 Промокод', callback_data='promo')
)

info_kb = InlineKeyboardMarkup(row_width=1).add(
    InlineKeyboardButton('🔥 Инструкция по использованию', callback_data='guide'),
    InlineKeyboardButton('🔙 Назад', callback_data='back_to_main_menu')
)

back_to_main_kb = InlineKeyboardMarkup().add(InlineKeyboardButton('🔙 Главное меню', callback_data='back_to_main_menu'))


### ОБРАБОТЧИКИ ДЕЙСТВИЙ ###

@dp.message_handler(commands=['start'])
async def cmd_start(message: types.Message):
    user_id = str(message.from_user.id)
    users = read_file_lines(config.USERS_FILE)

    if user_id not in users:
        await append_to_file(config.USERS_FILE, user_id)
        
        # Выдача пробного ключа
        free_key_url = await create_outline_key(message.from_user.id, gb_limit=3, name_prefix="FreeTrial")
        if free_key_url:
            moscow_time_str = (await get_moscow_time()).strftime('%Y-%m-%d %H:%M:%S')
            username = message.from_user.username or "N/A"
            await append_to_file(config.USERS_USERNAME_FILE, f'{user_id}|{username}|{moscow_time_str}|{free_key_url}|Free')
            
            welcome_text = (
                f"*Добро пожаловать, {message.from_user.first_name}! 👋*\n\n"
                f"🎉 В качестве подарка мы дарим вам *бесплатный ключ на 3 ГБ* трафика:\n\n"
                f"🗝️ Ваш ключ:\n`{free_key_url}`\n\n"
                f"Этот ключ поможет вам протестировать наш сервис. Когда трафик закончится, вы сможете приобрести новый.\n\n"
                f"ℹ️ Для начала работы, пожалуйста, ознакомьтесь с инструкцией в разделе *'Информация'*."
            )
            await message.answer(welcome_text, parse_mode=ParseMode.MARKDOWN)
        else:
            await message.answer("К сожалению, произошла ошибка при создании вашего пробного ключа. Пожалуйста, обратитесь в поддержку.")

    await message.answer(start_text, reply_markup=main_menu_kb, parse_mode=ParseMode.MARKDOWN)

@dp.callback_query_handler(text='back_to_main_menu')
async def cb_back_to_main_menu(call: types.CallbackQuery):
    await call.message.edit_text(start_text, reply_markup=main_menu_kb, parse_mode=ParseMode.MARKDOWN)
    await call.answer()

@dp.callback_query_handler(text='info')
async def cb_info(call: types.CallbackQuery):
    await call.message.edit_text(info_text, reply_markup=info_kb, parse_mode=ParseMode.MARKDOWN)
    await call.answer()

@dp.callback_query_handler(text='guide')
async def cb_guide(call: types.CallbackQuery):
    back_kb = InlineKeyboardMarkup().add(InlineKeyboardButton('🔙 Назад', callback_data='info'))
    await call.message.edit_text(guide_text, reply_markup=back_kb, parse_mode=ParseMode.MARKDOWN)
    await call.answer()



def get_buy_keyboard():
    kb = InlineKeyboardMarkup(row_width=2)
    buttons = [InlineKeyboardButton(f"{gb} ГБ - {price}₽", callback_data=f"buy_new_{gb}") for gb, price in config.PRICE_NEW.items() if gb < 998]
    buttons.append(InlineKeyboardButton(f"Безлимит (1 мес) - {config.PRICE_NEW[998]}₽", callback_data="buy_new_998"))
    buttons.append(InlineKeyboardButton(f"Безлимит (3 мес) - {config.PRICE_NEW[999]}₽", callback_data="buy_new_999"))
    kb.add(*buttons)
    kb.add(InlineKeyboardButton('🔙 Назад', callback_data='back_to_main_menu'))
    return kb

@dp.callback_query_handler(text='buy_vpn')
async def cb_buy_vpn(call: types.CallbackQuery):
    await call.message.edit_text("Выберите тариф для нового ключа:", reply_markup=get_buy_keyboard())
    await call.answer()

@dp.callback_query_handler(Text(startswith="buy_new_"))
async def cb_process_new_key_purchase(call: types.CallbackQuery):
    gb_limit = int(call.data.split('_')[2])
    price = config.PRICE_NEW.get(gb_limit)

    if not price:
        await call.answer("Тариф не найден.", show_alert=True)
        return
    
    if gb_limit >= 998:
        tariff_name = f"Безлимит ({'3 мес.' if gb_limit == 999 else '1 мес.'})"
    else:
        tariff_name = f"{gb_limit} ГБ"

    payment_description = f"Покупка VPN: {tariff_name}"
    payment = await generate_payment(price, call.from_user.id, payment_description)

    payment_kb = InlineKeyboardMarkup(row_width=1).add(
        InlineKeyboardButton('🔗 Перейти к оплате', url=payment.redirected_url),
        InlineKeyboardButton('✅ Я оплатил', callback_data=f"check_payment_:{payment.label}:{price}:{gb_limit}"),
        InlineKeyboardButton('🔙 Отмена', callback_data='buy_vpn')
    )
    
    await call.message.edit_text(
        f"Вы выбрали тариф: *{tariff_name}*.\nСумма к оплате: *{price} ₽*.\n\n"
        "Нажмите на кнопку ниже, чтобы перейти к оплате. После успешной оплаты вернитесь и нажмите 'Я оплатил'.",
        reply_markup=payment_kb,
        parse_mode=ParseMode.MARKDOWN
    )
    await call.answer()

@dp.callback_query_handler(Text(startswith="check_payment_")) # проверка оплаты
async def cb_check_payment(call: types.CallbackQuery):
    try:
        prefix, rest = call.data.split(':', 1)
        rest, gb_limit_str = rest.rsplit(':', 1)
        label, price_str = rest.rsplit(':', 1)
    except ValueError:
        logging.error(f"Не удалось разобрать callback_data: {call.data}")
        await call.answer("Произошла ошибка. Пожалуйста, попробуйте еще раз.", show_alert=True)
        return
    price = float(price_str)
    gb_limit = int(gb_limit_str)
    
    await call.answer("🔍 Проверяем вашу оплату...", show_alert=False)

    if await check_yoomoney_payment(label, price):
        await call.message.edit_text("✅ Оплата прошла успешно! Создаем ваш ключ...", reply_markup=None)
        
        new_key_url = await create_outline_key(call.from_user.id, gb_limit=gb_limit)
        
        if new_key_url:
            await append_to_file(config.TRANSACTION_LOGS_FILE, f"{call.from_user.id}|{price}|{gb_limit}GB|{label}")
            final_text = (
                f"🎉 Ваш новый VPN-ключ готов!\n\n"
                f"🗝️ Ключ доступа:\n`{new_key_url}`\n\n"
                f"Спасибо за покупку! Не забудьте ознакомиться с инструкцией, если вы делаете это в первый раз."
            )
            await call.message.answer(final_text, reply_markup=back_to_main_kb, parse_mode=ParseMode.MARKDOWN)
            
        else: # если оплата прошла, а ключ не создался
            error_text = "Произошла критическая ошибка при создании ключа после оплаты. Пожалуйста, немедленно свяжитесь с поддержкой и предоставьте этот код: `{label}`"
            await call.message.answer(error_text, reply_markup=back_to_main_kb)
            logger.critical(f"Ключ не создан для {call.from_user.id} после успешной оплаты {label}")
    else:
        await call.answer("❌ Оплата еще не поступила. Пожалуйста, подождите 1-2 минуты после оплаты и попробуйте снова.", show_alert=True)


@dp.callback_query_handler(text='my_keys') # при нажатии "мои ключи"
async def cb_my_keys(call: types.CallbackQuery):
    user_id = str(call.from_user.id)
    user_keys_info = []
    
    all_keys = read_file_lines(config.KEYS_IDS_FILE)
    user_keys_data = [line.split('||') for line in all_keys if line.startswith(user_id)]

    if not user_keys_data:
        await call.message.edit_text("У вас еще нет купленных ключей.", reply_markup=back_to_main_kb)
        await call.answer()
        return

    await call.answer("Загружаю информацию о ключах...")
    
    try:
        outline_keys = outline_client.get_keys()
        outline_keys_dict = {key.key_id: key for key in outline_keys}
        
        response_text = "*Ваши активные ключи:*\n\n"
        
        for key_data in user_keys_data:
            _, access_url, key_id = key_data
            
            if key_id in outline_keys_dict:
                key = outline_keys_dict[key_id]
                used_gb = key.used_bytes / (1024**3) if key.used_bytes else 0
                
                if key.data_limit:
                    limit_gb = key.data_limit / (1024**3)
                    response_text += (
                        f"🔹 *Ключ ID: {key.key_id}*\n"
                        f"   - Трафик: *{used_gb:.2f} / {limit_gb:.0f} ГБ*\n"
                        f"   - Ключ доступа: `{access_url}`\n\n"
                    )
                else: # Безлимитный
                    response_text += (
                        f"🔹 *Ключ ID: {key.key_id} (Безлимит)*\n"
                        f"   - Потрачено: *{used_gb:.2f} ГБ*\n"
                        f"   - Ключ доступа: `{access_url}`\n\n"
                    )
        
        await call.message.edit_text(response_text, reply_markup=back_to_main_kb, parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True)

    except Exception as e:
        logger.error(f"Ошибка при получении ключей для {user_id}: {e}")
        await call.message.edit_text("Не удалось загрузить информацию о ключах. Попробуйте позже.", reply_markup=back_to_main_kb)

### Поддержка ###

@dp.callback_query_handler(text='support')
async def cb_support(call: types.CallbackQuery):
    """Начинает диалог с поддержкой."""
    await SupportState.waiting_for_message.set()
    cancel_kb = ReplyKeyboardMarkup(resize_keyboard=True).add("Отмена")
    await call.message.answer(support_text, reply_markup=cancel_kb, parse_mode=ParseMode.MARKDOWN)
    await call.answer()

@dp.message_handler(state=SupportState.waiting_for_message, content_types=types.ContentTypes.ANY)
async def process_support_message(message: types.Message, state: FSMContext):
    """Пересылает сообщение от пользователя администратору."""
    if message.text and message.text.lower() == 'отмена':
        await state.finish()
        await message.answer("Действие отменено.", reply_markup=ReplyKeyboardRemove())
        await message.answer(start_text, reply_markup=main_menu_kb, parse_mode=ParseMode.MARKDOWN)
        return
        
    forward_kb = InlineKeyboardMarkup().add(
        InlineKeyboardButton("Ответить пользователю", callback_data=f"admin_answer_{message.from_user.id}")
    )
    await bot.forward_message(config.support_account, message.chat.id, message.message_id)
    await bot.send_message(config.support_account, f"Сообщение от {message.from_user.full_name} (ID: {message.from_user.id})", reply_markup=forward_kb)
    
    await message.answer("Ваше сообщение отправлено в поддержку. Ожидайте ответа.", reply_markup=ReplyKeyboardRemove())
    await state.finish()
        

@dp.callback_query_handler(Text(startswith="admin_answer_"), state="*")
async def cb_admin_answer(call: types.CallbackQuery, state: FSMContext):
    user_id_to_answer = call.data.split('_')[2]
    await SupportState.admin_response.set()
    await state.update_data(user_id=user_id_to_answer) # Сохраняем ID пользователя для ответа

    await call.message.answer(f"Введите ваш ответ для пользователя с ID: {user_id_to_answer}")
    await call.answer()

@dp.message_handler(state=SupportState.admin_response)
async def process_admin_response(message: types.Message, state: FSMContext):
    data = await state.get_data()
    user_id = data.get('user_id')

    if not user_id:
        await message.answer("Произошла ошибка, ID пользователя не найден. Попробуйте снова.")
        await state.finish()
        return

    try:
        await bot.send_message(user_id, f"Сообщение от поддержки:\n\n{message.text}")
        await message.answer(f"✅ Ваше сообщение успешно отправлено пользователю {user_id}.")
    except Exception as e:
        logger.error(f"Не удалось отправить ответ от админа пользователю {user_id}: {e}")
        await message.answer(f"❌ Не удалось отправить сообщение. Ошибка: {e}")
    
    await state.finish()


### Промокоды ###
@dp.callback_query_handler(text='promo')
async def cb_promo(call: types.CallbackQuery):
    """Запрашивает ввод промокода."""
    await PromoCodeState.waiting_for_code.set()
    await call.message.edit_text("Введите ваш промокод:")
    await call.answer()

@dp.message_handler(state=PromoCodeState.waiting_for_code) # проверяет введеный промокод
async def process_promo_code(message: types.Message, state: FSMContext):
    await state.finish()
    user_id = str(message.from_user.id)
    user_code = message.text.strip()

    # проверяем, не активировал ли пользователь промокод ранее
    activation_logs = read_file_lines(config.PROMO_ACTIVATION_LOGS)
    for log in activation_logs:
        if log.startswith(user_id):
            await message.answer("❌ Вы уже активировали промокод.", reply_markup=back_to_main_kb)
            return

    # проверяем, существует ли такой промокод
    all_promocodes = read_file_lines(config.PROMOCODES_FILE)
    if user_code not in all_promocodes:
        await message.answer("❌ Такого промокода не существует или он уже был использован.", reply_markup=back_to_main_kb)
        return

    # находим ключ пользователя
    all_keys = read_file_lines(config.KEYS_IDS_FILE)
    user_key_id = None
    for key_line in all_keys:
        if key_line.startswith(user_id):
            user_key_id = key_line.split('||')[2]

    if not user_key_id:
        await message.answer("❌ Не удалось найти ваш активный ключ для начисления бонуса.", reply_markup=back_to_main_kb)
        return

    try:
        # получаем бонус из файла, обновляем лимит
        bonus_gb_str = read_file(os.path.join(config.PROMO_DISCOUNT_DIR, f"{user_code}.txt"))
        if not bonus_gb_str:
             await message.answer("❌ Ошибка конфигурации промокода. Сообщите администратору.", reply_markup=back_to_main_kb)
             logger.error(f"Не найден файл скидки для промокода {user_code}")
             return

        bonus_gb = int(bonus_gb_str)
        bonus_bytes = bonus_gb * 1024 * 1024 * 1024

        key_details = outline_client.get_keys(key_id=user_key_id)[0]
        current_limit_bytes = key_details.data_limit if key_details.data_limit else 0
        new_limit_bytes = current_limit_bytes + bonus_bytes

        # устанавливаем новый лимит
        if outline_client.add_data_limit(user_key_id, new_limit_bytes):
            await append_to_file(config.PROMO_ACTIVATION_LOGS, f"{user_id}||{user_code}")
            await message.answer(f"✅ Промокод '{user_code}' успешно активирован! Вам начислено *{bonus_gb} ГБ* трафика.", reply_markup=back_to_main_kb, parse_mode=ParseMode.MARKDOWN)
        else:
            raise Exception("Outline client failed to set new data limit.")

    except Exception as e:
        logger.error(f"Ошибка при активации промокода {user_code} для пользователя {user_id}: {e}")
        await message.answer("❌ Произошла непредвиденная ошибка при активации промокода. Пожалуйста, обратитесь в поддержку.", reply_markup=back_to_main_kb)

### Фоновая задача: Уведомления и отчистка ###

async def check_keys_and_notify(): # уведомляет об истечении срока; о малом остатке трафика; удаляет истекшие и исчерпанные ключи
    await asyncio.sleep(10)
    while True:
        try:
            logger.info("Запущена периодическая проверка ключей...")
            current_time_unix = int(time.time())
            all_outline_keys = outline_client.get_keys()
            expirations = read_file_lines(config.USERS_KEYS_EXPIRATIONS_FILE)
            notified_users = read_file_lines(config.NOTIFIED_KEYS_FILE)

            # Словари для быстрого доступа
            outline_keys_dict = {key.key_id: key for key in all_outline_keys}
            
            new_expirations = []
            keys_to_delete = []

            for line in expirations:
                try:
                    user_id, expiration_unix, key_id = line.split('||')
                    expiration_unix = int(expiration_unix)
                    
                    # Проверка на удаление
                    if key_id not in outline_keys_dict or expiration_unix < current_time_unix:
                        keys_to_delete.append((key_id, user_id, "срок действия истек"))
                        continue

                    outline_key = outline_keys_dict[key_id]
                    used_bytes = outline_key.used_bytes if outline_key.used_bytes is not None else 0
                    
                    # Проверка лимита трафика
                    if outline_key.data_limit and used_bytes >= outline_key.data_limit:
                        keys_to_delete.append((key_id, user_id, "лимит трафика исчерпан"))
                        continue

                    # Логика уведомлений (один раз)
                    if key_id not in notified_users:
                        # Уведомление об окончании срока (за 3 дня)
                        if expiration_unix - current_time_unix < 259200: # 3 дня
                             await bot.send_message(user_id, read_file(config.NOTIFY_EXPIRATION_FILE))
                             await append_to_file(config.NOTIFIED_KEYS_FILE, key_id)

                        # Уведомление о малом трафике (осталось < 10%)
                        elif outline_key.data_limit and (outline_key.data_limit - used_bytes) / outline_key.data_limit < 0.1:
                            await bot.send_message(user_id, read_file(config.NOTIFY_LOW_TRAFFIC_FILE))
                            await append_to_file(config.NOTIFIED_KEYS_FILE, key_id)
                    
                    new_expirations.append(line)

                except Exception as e:
                    logger.error(f"Ошибка при обработке строки ключа '{line}': {e}")
                    new_expirations.append(line) # Сохраняем строку, чтобы не потерять данные

            # Удаление ключей
            for key_id, user_id, reason in keys_to_delete:
                try:
                    outline_client.delete_key(key_id)
                    await bot.send_message(user_id, f"Ваш ключ был удален, так как {reason}. Вы можете приобрести новый в главном меню.")
                    logger.info(f"Удален ключ {key_id} пользователя {user_id}. Причина: {reason}.")
                except Exception as e:
                    logger.error(f"Не удалось удалить ключ {key_id}: {e}")
            
            # Перезапись файла с актуальными ключами
            with open(config.USERS_KEYS_EXPIRATIONS_FILE, 'w', encoding='utf-8') as f:
                f.write('\n'.join(new_expirations) + '\n')

        except Exception as e:
            logger.error(f"Критическая ошибка в фоновой задаче `check_keys_and_notify`: {e}")

        await asyncio.sleep(3600)

async def on_startup(dp: Dispatcher):
    logger.info("Бот запускается...")
    ensure_dirs_exist()
    asyncio.create_task(check_keys_and_notify())
    logger.info("Фоновая задача проверки ключей запущена.")

if __name__ == '__main__':
    executor.start_polling(dp, skip_updates=True, on_startup=on_startup)