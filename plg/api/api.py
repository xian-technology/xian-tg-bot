from plugin import TGBFPlugin
from starlette.responses import JSONResponse


class Api(TGBFPlugin):

    async def init(self):
        await self.add_endpoint('/address/{address_id}', self.address)

    async def address(self, telegram_id: str):
        try:
            int(telegram_id)
        except ValueError:
            response = {
                'error': 'Invalid Telegram ID',
                'success': False,
                'public_key': ''
            }
            return JSONResponse(content=response)

        sql = await self.get_resource('select_address.sql')
        data = await self.exec_sql_global(sql, int(telegram_id))

        address = data['data']

        if address:
            response = {
                'error': '',
                'success': True,
                'public_key': address[0][0]
            }
        else:
            response = {
                'error': 'Unknown Telegram ID',
                'success': False,
                'public_key': ''
            }

        return JSONResponse(content=response)
