from uuid import UUID

from aiogram import Bot

from quantum import settings
from quantum.connectors import db_printing, db_users
from quantum.core import db
from quantum.core.exceptions import BusinessLogicFucked
from quantum.core.globals import GlobalValue
from quantum.entities.printing import PrintingTask, PrintingTaskStatus
from quantum.entities.web import CompletionStatus
from quantum.services import client_notification


async def calculate_cost(filepath: str) -> int:
    return 420


async def process_file(user_id: int, file_id: str, message_id: int) -> UUID:
    """
    Скачиваем файл, считаем стоимость, создаём таску на печать

    :param int user_id: айдишник пользователя
    :param str file_id: идентификатор файла из aiogram
    :returns: стоимость печати
    """
    task = await db_printing.create_printing_task(user_id, file_id, message_id)

    bot: Bot = GlobalValue[Bot].get()
    file = await bot.get_file(file_id)
    if file.file_path is None:
        raise BusinessLogicFucked(msg=['FILE_GETTING_ERROR'])

    downloaded_file_path = f'{settings.FILESTORAGE_PATH}/{task.id}.pdf'
    await bot.download_file(file_path=file.file_path, destination=downloaded_file_path)

    cost_cents = await calculate_cost(downloaded_file_path)
    await db_printing.set_printing_cost(task.id, cost_cents)

    await db_printing.set_task_status([task.id], PrintingTaskStatus.parameters_input)

    return task.id


async def schedule_printing(printing_task_id: UUID):
    bot: Bot = GlobalValue[Bot].get()
    printing_task = await db_printing.get_by_id(printing_task_id)

    async with db.transaction():
        if not db_users.check_if_enough_money(printing_task.user_id, printing_task.cost_cents):
            await bot.send_message(
                text='нужно больше злотых',
                chat_id=printing_task.user_id,
                reply_to_message_id=printing_task.message_id,
            )
            return

        await db_users.write_off_user_balance(printing_task.user_id, printing_task.cost_cents)

        await db_printing.set_task_status([printing_task.id], PrintingTaskStatus.printing)

        await bot.send_message(
            text='файлик отправлен на печать',
            chat_id=printing_task.user_id,
            reply_to_message_id=printing_task.message_id,
        )


async def try_get_next_task(printer_id: int) -> PrintingTask | None:
    """
    Пытаемся получить таску для печати и проверяем, что файл можно скачать
    """

    mb_task: PrintingTask | None = await db_printing.try_get_next_task(printer_id)

    if mb_task is None:
        return None

    # если файл есть в /tmp (да, это лучшее, что я придумал)
    #   return

    # если файл есть на диске
    #   cp /path/to/drive/file-id.pdf /tmp/.
    #   return

    # скачиваем файл из тг в /tmp/file-id.pdf
    # return

    return mb_task


async def update_status_then_notify(task_id: UUID, status: CompletionStatus):
    status_map: dict[CompletionStatus, PrintingTaskStatus] = {
        CompletionStatus.success: PrintingTaskStatus.done,
        CompletionStatus.failed: PrintingTaskStatus.failed,
    }
    await db_printing.set_task_status(printing_task_ids=[task_id], new_status=status_map[status])

    task = await db_printing.get_by_id(printing_task_id=task_id)
    if status == CompletionStatus.failed:
        # TODO: надо спамить в техподдержку, а не в клиента
        await client_notification.send_printing_failed(
            user_id=task.user_id,
            message_id=task.message_id,
        )
    else:
        await client_notification.send_printing_complete(
            user_id=task.user_id,
            message_id=task.message_id,
        )
