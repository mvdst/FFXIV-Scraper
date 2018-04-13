from werkzeug.urls import url_quote_plus
from gevent.pool import Pool
from datetime import datetime
import bs4
import re
import requests
import math


FFXIV_EQUIPMENT_SLOTS = [
    'Main Hand', 'Off Hand', 'Head', 'Body',
    'Hands', 'Waist', 'Legs', 'Feet', 'Earrings',
    'Necklace', 'Bracelets', 'Ring 1', 'Ring 2',
    'Soul Crystal'
]


class DoesNotExist(Exception):
    pass


class Scraper(object):
    def __init__(self):
        self.s = requests.Session()

    def update_headers(self, headers):
        self.s.headers.update(headers)

    def make_request(self, url=None):
        return self.s.get(url)


class FFXIvScraper(Scraper):

    def __init__(self):
        super(FFXIvScraper, self).__init__()
        headers = {
            'Accept-Language': 'en-us,en;q=0.5',
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_8_4) Chrome/27.0.1453.116 Safari/537.36',
        }
        self.update_headers(headers)
        self.lodestone_domain = 'na.finalfantasyxiv.com'
        self.lodestone_url = 'http://%s/lodestone' % self.lodestone_domain

    '''
    Scaper method to grab the news items
    from the lodestone website.
    '''
    def scrape_topics(self):
        url = self.lodestone_url + '/topics/'
        r = self.make_request(url)

        news = []
        soup = bs4.BeautifulSoup(r.content, "html.parser")
        for tag in soup.select('.news__content ul li.news__list--topics'):
            entry = {}
            title_tag = tag.select('.news__list--title a')[0]
            script = str(tag.select('script')[0])
            entry['timestamp'] = datetime.fromtimestamp(int(re.findall(r"1[0-9]{9},", script)[0]
                                                            .rstrip(','))).strftime('%Y-%m-%dT%H:%M:%S')
            entry['link'] = '//' + self.lodestone_domain + title_tag['href']
            entry['id'] = entry['link'].split('/')[-1]
            entry['title'] = title_tag.string.strip()
            body = tag.select('.news__list--banner')[0]
            for a in body.findAll('a'):
                if a['href'].startswith('/'):
                    a['href'] = '//' + self.lodestone_domain + a['href']
            entry['body'] = body.text
            entry['lang'] = 'en'
            news.append(entry)
        return news

    '''
    Converts a server and character name to a usable 
    lodestone id for use as a uri component.
    '''
    def validate_character(self, server_name, character_name):

        # Search for character
        url = self.lodestone_url + '/character/?q=%s&worldname=%s' \
                                   % (url_quote_plus(character_name), server_name)

        r = self.make_request(url=url)

        if not r:
            return None

        soup = bs4.BeautifulSoup(r.content, "html.parser")

        for tag in soup.select('p.entry__name'):
            if tag.string.lower() == character_name.lower():
                return {
                    'lodestone_id': tag.parent.parent['href'].split('/')[3],
                    'name': str(tag.string),
                }

        return None

    '''
    Converts a server and company name to a usable 
    lodestone id for use as a uri component.
    '''
    def validate_free_company(self, server_name, free_company_name):

        # Search for free company
        url = self.lodestone_url + '/freecompany/?q=%s&worldname=%s' \
                                    % (url_quote_plus(free_company_name), server_name)

        r = self.make_request(url=url)

        if not r:
            return None

        soup = bs4.BeautifulSoup(r.content, "html.parser")

        for tag in soup.select('p.entry__name'):
            if tag.string.lower() == free_company_name.lower():
                return {
                    'lodestone_id': tag.find_parent('a', class_='entry__block')['href'].split('/')[3],
                    'name': str(tag.string),
                }

        return None

    '''
    Verifies a character based on a specific verification
    code entered into the bio information.    
    '''
    def verify_character(self, server_name, character_name, verification_code, lodestone_id=None):
        if not lodestone_id:
            char = self.validate_character(server_name, character_name)
            if not char:
                raise DoesNotExist()
            lodestone_id = char['lodestone_id']

        url = self.lodestone_url + '/character/%s/' % lodestone_id

        r = self.make_request(url=url)

        if not r:
            return False

        soup = bs4.BeautifulSoup(r.content, "html.parser")

        page_name = soup.select('p.frame__chara__name')[0].text.strip()
        page_server = soup.select('p.frame__chara__world')[0].text.strip()

        if page_name != character_name or page_server != server_name:
            return False

        return lodestone_id if soup.select('div.character__selfintroduction')[
                                   0].text.strip() == verification_code else False

    '''
    Main scraping method for grabbing 
    character related information.
    '''
    def scrape_character(self, lodestone_id):
        character_url = self.lodestone_url + '/character/%s/' % lodestone_id

        r = self.make_request(url=character_url)

        if not r:
            raise DoesNotExist()

        soup = bs4.BeautifulSoup(r.content, "html.parser")

        character_link = '/lodestone/character/%s/' % lodestone_id
        if character_link not in soup.select('a.frame__chara__link')[0]['href']:
            raise DoesNotExist()

        # Name, Server, Title
        name = soup.select('p.frame__chara__name')[0].text.strip()
        server = soup.select('p.frame__chara__world')[0].text.strip()

        try:
            title = soup.select('p.frame__chara__title')[0].text.strip()
        except (AttributeError, IndexError):
            title = None

        # Race, Tribe, Gender
        demographics = soup.find(text='Race/Clan/Gender').parent.parent
        demographics.select('p.character-block__name')[0].select('br')[0].replace_with(' / ')
        race, clan, gender = demographics.select('p.character-block__name')[0].text.split(' / ')
        gender = 'male' if gender.strip('\n\t')[-1] == u'\u2642' else 'female'

        # Nameday & Guardian
        nameday_guardian_block = soup.find(text='Nameday').parent.parent
        nameday = nameday_guardian_block.select('p.character-block__birth')[0].text
        guardian = nameday_guardian_block.select('p.character-block__name')[0].text

        # City-state
        citystate = soup.find(text='City-state').parent.parent.select('p.character-block__name')[0].text

        # Grand Company
        try:
            grand_company = soup.find_all(text='Grand Company')[1].parent.parent.select('p.character-block__name')[
                0].text.split('/')
        except (AttributeError, IndexError):
            grand_company = None

        # Free Company
        try:
            free_company_name_block = soup.select('div.character__freecompany__name')[0].find('h4').find('a')
            free_company_crest_block = soup.select('div.character__freecompany__crest__image')[0]
            free_company = {
                'id': re.findall('(\d+)', free_company_name_block['href'])[0],
                'name': free_company_name_block.text,
                'crest': [x['src'] for x in free_company_crest_block.findChildren('img')]
            }
        except (AttributeError, IndexError):
            free_company = None

        # Classes
        classes = {}
        for class_type in soup.select('ul.character__job'):
            for job in class_type.find_all('li'):
                job_name = job.select('div.character__job__name')[0].text
                job_level = job.select('div.character__job__level')[0].text
                job_exp_meter = job.select('div.character__job__exp')[0].text
                job_exp = 0
                job_exp_next = 0
                if job_level == '-':
                    job_level = 0
                else:
                    job_level = int(job_level)
                    job_exp, job_exp_next = job_exp_meter.split(' / ')

                classes[job_name] = dict(level=job_level, exp=job_exp, exp_next=job_exp_next)

        # Stats
        stats = {}

        param_blocks = soup.select('table.character__param__list')

        for param_block in param_blocks:
            stat_names = param_block.select('span')
            for stat_name_th in stat_names:
                stat_name = stat_name_th.text
                stat_val = stat_name_th.parent.next_sibling.text
                stats[stat_name] = stat_val

        for attribute in ('hp', 'mp', 'tp'):
            try:
                stats[attribute] = int(
                    soup.select('p.character__param__text__' + attribute + '--en-us')[0].next_sibling.text)
            except IndexError:
                pass

        mounts = []
        mount_box = soup.select('div.character__mounts')[0]
        for mount in mount_box.select('li'):
            mount_name = mount.select('div.character__item_icon')[0].get("data-tooltip")
            mounts.append(mount_name)

        minions = []
        minion_box = soup.select('div.character__minion')[0]
        for minion in minion_box.select('li'):
            minion_name = minion.select('div.character__item_icon')[0].get("data-tooltip")
            minions.append(minion_name)

        # Equipment
        parsed_equipment = []

        equip_boxes = soup.select('.ic_reflection_box')
        for equip_box in equip_boxes[:len(equip_boxes) // 2]:
            # Grab the slot number
            r = re.compile(r"([1-9]\d*|0)").search(str(equip_box['class'][0]))

            parsed_equip = {}
            parsed_equip['slot'] = FFXIV_EQUIPMENT_SLOTS[int(r.group(1))]
            parsed_equip['name'] = equip_box.select('h2.db-tooltip__item__name')[0].text \
                if equip_box.select('h2.db-tooltip__item__name') else 'Empty'
            parsed_equip['img'] = equip_box.select('img.db-tooltip__item__icon__item_image')[0]['src'] \
                if equip_box.select('img.db-tooltip__item__icon__item_image') else ''

            materia_sockets = equip_box.find_all(class_='socket')
            materias = {}
            for materia in materia_sockets:
                slot_number = str(len(materias) + 1)
                materias['slot_' + slot_number] = materia.next_sibling(text=True, recursive=False)[0] \
                    if materia.next_sibling is not None else '-'
            # Create the remainder of possible slots
            for x in range(len(materias)+1, 6):
                materias['slot_' + str(x)] = '-'
            parsed_equip['materia'] = materias

            glamour = equip_box.select('div.db-tooltip__item__mirage > p')
            parsed_equip['glamour'] = glamour[0].text if len(glamour) > 0 else 'None'

            parsed_equipment.append(parsed_equip)

        equipment = parsed_equipment

        data = {
            'name': name,
            'server': server,
            'title': title,

            'race': race,
            'clan': clan,
            'gender': gender,

            'legacy': len(soup.select('.bt_legacy_history')) > 0,

            'avatar_url': soup.select('div.character__detail__image')[0].select('img')[0]['src'],
            'portrait_url': soup.select('div.frame__chara__face')[0].select('img')[0]['src'],

            'nameday': nameday,
            'guardian': guardian,

            'citystate': citystate,

            'grand_company': grand_company,
            'free_company': free_company,

            'classes': classes,
            'stats': stats,

            'achievements': self.scrape_achievements(lodestone_id),

            'minions': minions,
            'mounts': mounts,

            'current_equipment': equipment,
        }

        return data

    '''
    Scraper method to 
    retrieve achievements.
    '''
    def scrape_achievements(self, lodestone_id, page=1):
        url = self.lodestone_url + '/character/%s/achievement/?filter=2&page=%s' \
              % (lodestone_id, page)

        r = self.make_request(url)

        if not r:
            return {}

        soup = bs4.BeautifulSoup(r.content, "html.parser")

        achievements = {}
        ach_block = soup.select('div.ldst__achievement')[0]
        for tag in ach_block.select('li.entry'):
            achievement = {
                'id': int(tag.select('a.entry__achievement')[0]['href'].split('/')[-2]),
                'icon': tag.select('div.entry__achievement__frame')[0].select('img')[0]['src'],
                'name': tag.select('p.entry__activity__txt')[0].text.split('"')[1],
                'date': datetime.fromtimestamp(int(re.findall(r'ldst_strftime\((\d+),', tag.find('script').text)[0]
                                                   )).strftime('%Y-%m-%dT%H:%M:%S')
            }
            achievements[achievement['id']] = achievement

        try:
            pages = int(math.ceil(float(int(soup.select('.parts__total')[0].text.split(' ')[0]) / 20)))
        except (ValueError, IndexError):
            pages = 0

        if pages > page:
            achievements.update(self.scrape_achievements(lodestone_id, page + 1))

        return achievements

    '''
    Main scraper method to 
    retrieve fc based data.
    '''
    def scrape_free_company(self, lodestone_id):
        url = self.lodestone_url + '/freecompany/%s/' % lodestone_id
        html = self.make_request(url)

        if not html:
            raise DoesNotExist()

        soup = bs4.BeautifulSoup(html.content, "html.parser")

        fc_tag = soup.select('p.freecompany__text__tag')[0].text
        fc_tag = fc_tag[1:-1] if fc_tag else ''

        crest = [x['src'] for x in
                 soup.find('div', attrs={'class': 'entry__freecompany__crest__image'}).findChildren('img')]

        formed = soup.find(string="Formed").parent.next_sibling.next_sibling.text
        if formed:
            m = re.search(r'ldst_strftime\(([0-9]+),', formed)
            if m.group(1):
                formed = datetime.fromtimestamp(float(m.group(1))).strftime('%Y-%m-%dT%H:%M:%S')
        else:
            formed = None

        slogan = soup.select('p.freecompany__text__message')[0].text
        slogan = ''.join(x.replace('<br/>', '\n') for x in slogan) if slogan else ""
        active = soup.find(text='Active').parent.next_sibling.next_sibling.text.strip()
        recruitment = soup.find(text='Recruitment').parent.next_sibling.next_sibling.text.strip()
        active_members = soup.find(text='Active Members').parent.next_sibling.next_sibling.text.strip()
        rank = soup.find(text='Rank').parent.next_sibling.next_sibling.text.strip()

        focus = []
        for f in soup.select('.freecompany__focus_icon li'):
            on = not (f.get('class') and 'freecompany__focus_icon--off' in f.get('class'))
            focus.append(dict(on=on,
                              name=f.find('p').text,
                              icon=f.find('img')['src']))

        seeking = []
        for f in soup.select('.freecompany__focus_icon--role li'):
            on = not (f.get('class') and 'freecompany__focus_icon_off' in f.get('class'))
            seeking.append(dict(on=on,
                                name=f.find('p').text,
                                icon=f.find('img')['src']))

        estate_block = []
        # the estate data has no container, regex was the only way to grab it
        for item in soup.find_all(True, {"class": re.compile("^(freecompany__estate__)(?!title).+")}):
            estate_block += item

        if estate_block[0] != 'No Estate or Plot':
            estate = dict()
            estate['name'] = estate_block[0]
            estate['address'] = estate_block[1]

            greeting = estate_block[2]
            estate['greeting'] = ''.join(x.replace('<br/>', '\n') for x in greeting) if greeting else ""
        else:
            estate = None

        url = self.lodestone_url + '/freecompany/%s/member/' % lodestone_id
        html = self.make_request(url).content

        if 'The page you are searching for has either been removed,' in str(html):
            raise DoesNotExist()

        soup = bs4.BeautifulSoup(html, "html.parser")

        try:
            name = soup.select('p.entry__freecompany__name')[0].text.strip()
            server = soup.select('p.entry__freecompany__gc')[1].text.strip()
            grand_company = soup.select('p.entry__freecompany__gc')[0].text.strip()
        except IndexError:
            raise DoesNotExist()

        roster = []

        '''
        Helper function to return all members 
        belonging to the queried company.
        '''
        def populate_roster(roster_page=1, soup=None):
            if not soup:
                r = self.make_request(url + '?page=%s' % roster_page)
                soup = bs4.BeautifulSoup(r.content, "html.parser")

            for tag in soup.select('li.entry'):
                if not tag.find('img'):
                    continue

                member = {
                    'name': tag.select('p.entry__name')[0].text,
                    'lodestone_id': tag.select('a.entry__bg')[0]['href'].split('/')[3],
                    'rank': {
                        # 'id': int(re.findall('class/(\d+?)\.png', tag.find('img')['src'])[0]),
                        'id': 1,
                        'name': tag.select('ul.entry__freecompany__info')[0].select('span')[0].text.strip(),
                    },
                }

                if member['rank']['id'] == 0:
                    member['leader'] = True

                roster.append(member)

        populate_roster(soup=soup)

        try:
            pages = int(soup.find(attrs={'rel': 'last'})['href'].rsplit('=', 1)[-1])
        except TypeError:
            pages = 1

        if pages > 1:
            pool = Pool(5)
            for page in range(2, pages + 1):
                pool.spawn(populate_roster, page)
            pool.join()

        return {
            'name': name,
            'server': server.lower(),
            'grand_company': grand_company,
            'roster': roster,
            'slogan': slogan,
            'tag': fc_tag,
            'formed': formed,
            'crest': crest,
            'active': active,
            'recruitment': recruitment,
            'active_members': active_members,
            'rank': rank,
            'focus': focus,
            'seeking': seeking,
            'estate': estate
        }
