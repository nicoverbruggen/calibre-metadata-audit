# SPDX-License-Identifier: GPL-3.0-only
"""Read EPUB metadata and change only the fields selected by the user."""

from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from io import BytesIO
import posixpath
import re
import unicodedata
from urllib.parse import unquote
from zipfile import ZipFile

from lxml import etree


NS = {'o': 'http://www.idpf.org/2007/opf',
      'dc': 'http://purl.org/dc/elements/1.1/',
      'c': 'urn:oasis:names:tc:opendocument:xmlns:container'}
FIELDS = ('title', 'authors', 'series', 'publisher')


def parse_xml(data):
    parser = etree.XMLParser(resolve_entities=False, no_network=True)
    root = etree.fromstring(data, parser)
    if root.getroottree().docinfo.internalDTD is not None:
        raise ValueError('An internal XML DTD is not supported for metadata updates.')
    return root


def normal(value):
    value = unicodedata.normalize('NFC', value or '')
    return re.sub(r'\s+', ' ', value.translate(str.maketrans({'’': "'", '‘': "'"}))).strip().casefold()


def number(value):
    try:
        value = Decimal(str(value))
        if not value.is_finite():
            raise ValueError('Invalid series number')
        return format(value.normalize(), 'f')
    except InvalidOperation as err:
        raise ValueError('Invalid series number: ' + str(value)) from err


def target_metadata(mi):
    return {'title': mi.title or '', 'authors': list(mi.authors or []),
            'publisher': mi.publisher or '', 'series': mi.series or '',
            'series_index': number(mi.series_index) if mi.series else None}


def archive_metadata(data):
    with ZipFile(BytesIO(data)) as z:
        counts = Counter(z.namelist())
        if any(n != 1 for n in counts.values()):
            raise ValueError('Duplicate ZIP entries; inspect this EPUB manually.')
        container = parse_xml(z.read('META-INF/container.xml'))
        roots = container.xpath('//c:rootfile', namespaces=NS)
        if len(roots) != 1:
            raise ValueError('Expected one EPUB package document.')
        path = unquote(roots[0].get('full-path', ''))
        if not path or path.startswith('/') or '..' in path.split('/'):
            raise ValueError('Invalid package document path.')
        root = parse_xml(z.read(path))
    metadata = root.find('o:metadata', NS)
    if metadata is None:
        raise ValueError('Missing EPUB metadata.')
    return path, root, metadata


def text(element):
    return ''.join(element.itertext()).strip()


def refines(metadata, element, prop):
    ident = element.get('id')
    if not ident:
        return []
    return [x for x in metadata.findall('o:meta', NS)
            if x.get('refines') == '#' + ident and x.get('property') == prop]


def author_elements(metadata):
    result = []
    for element in metadata.findall('dc:creator', NS):
        roles = [text(x).lower().rsplit(':', 1)[-1] for x in refines(metadata, element, 'role')]
        old_role = element.get('{%s}role' % NS['o'])
        if old_role:
            roles.append(old_role.lower())
        if not roles or 'aut' in roles:
            result.append(element)
    return result


def series_elements(metadata):
    metas = metadata.findall('o:meta', NS)
    return [x for x in metas if x.get('property') == 'belongs-to-collection'
            and any(normal(text(t)) == 'series' for t in refines(metadata, x, 'collection-type'))]


def read_values(metadata):
    titles = metadata.findall('dc:title', NS)
    main = next((x for x in titles if any(text(t) == 'main' for t in refines(metadata, x, 'title-type'))),
                titles[0] if titles else None)
    subtitles = [text(x) for x in titles if x is not main and
                 any(text(t) == 'subtitle' for t in refines(metadata, x, 'title-type'))]
    title = text(main) if main is not None else ''
    combined = ': '.join([title] + subtitles) if title else ''
    authors = [text(x) for x in author_elements(metadata)]
    extra = [text(x) for x in metadata.findall('o:meta', NS)
             if x.get('property') == 'dcterms:creator']
    series = []
    for x in metadata.findall('o:meta', NS):
        if x.get('name') == 'calibre:series':
            index = next((i.get('content') for i in metadata.findall('o:meta', NS)
                          if i.get('name') == 'calibre:series_index'), None)
            series.append((x.get('content') or '', number(index) if index is not None else None))
    for x in series_elements(metadata):
        index = refines(metadata, x, 'group-position')
        series.append((text(x), number(text(index[0])) if index else None))
    return {'title': title, 'combined_title': combined,
            'extra_titles': [text(x) for x in metadata.findall('o:meta', NS) if x.get('property') == 'dcterms:title'],
            'extra_publishers': [text(x) for x in metadata.findall('o:meta', NS) if x.get('property') == 'dcterms:publisher'],
            'authors': authors,
            'extra_authors': extra, 'series': series,
            'publisher': [text(x) for x in metadata.findall('dc:publisher', NS)]}


def same_authors(actual, expected):
    if [normal(x) for x in actual] == [normal(x) for x in expected]:
        return True
    # Some publishers store multiple credited people in a single creator element.
    # Treat that as equal only when the complete names and order match Calibre.
    joined = normal(' & '.join(actual).replace(' and ', ' & '))
    return joined == normal(' & '.join(expected)) or (
        len(actual) == 1 and normal(actual[0]) == normal(', '.join(expected)))


def differences(data, target):
    _, _, metadata = archive_metadata(data)
    values = read_values(metadata)
    result = []
    if normal(target['title']) not in {normal(values['title']), normal(values['combined_title'])} or any(
            normal(x) not in {normal(target['title']), normal(values['combined_title'])} for x in values['extra_titles']):
        value = values['combined_title'] or '(missing)'
        if values['extra_titles']:
            value += '\nAdditional title: ' + ' | '.join(values['extra_titles'])
        result.append({'field': 'title', 'epub': value,
                       'database': target['title'] or '(missing)'})
    if not same_authors(values['authors'], target['authors']) or any(
            not same_authors([x], target['authors']) for x in values['extra_authors']):
        value = ' & '.join(values['authors']) or '(missing)'
        if values['extra_authors']:
            value += '\nAdditional creator: ' + ' | '.join(values['extra_authors'])
        result.append({'field': 'authors', 'epub': value,
                       'database': ' & '.join(target['authors']) or '(missing)'})
    wanted = {(normal(target['series']), target['series_index'])} if target['series'] else set()
    actual = {(normal(name), index) for name, index in values['series']}
    if actual != wanted:
        result.append({'field': 'series', 'epub': ' | '.join(
            f'{name} [{index if index is not None else "no number"}]' for name, index in values['series']) or '(none)',
            'database': f'{target["series"]} [{target["series_index"]}]' if target['series'] else '(none)'})
    if [normal(x) for x in values['publisher']] != ([normal(target['publisher'])] if target['publisher'] else []) or any(
            normal(x) != normal(target['publisher']) for x in values['extra_publishers']):
        value = ' | '.join(values['publisher']) or '(missing)'
        if values['extra_publishers']:
            value += '\nAdditional publisher: ' + ' | '.join(values['extra_publishers'])
        result.append({'field': 'publisher', 'epub': value,
                       'database': target['publisher'] or '(missing)'})
    return result


def remove_with_refinements(metadata, element):
    ident = element.get('id')
    pending = [element]
    if ident:
        pending.extend(x for x in metadata.findall('o:meta', NS) if x.get('refines') == '#' + ident)
    for x in pending:
        metadata.remove(x)


def append(metadata, namespace, tag, value=None, **attrs):
    element = etree.SubElement(metadata, '{%s}%s' % (namespace, tag), attrib=attrs)
    element.text = value
    return element


def unique_id(root, base):
    used = set(root.xpath('//@id'))
    candidate, i = base, 1
    while candidate in used:
        candidate, i = f'{base}-{i}', i + 1
    return candidate


def rewrite(data, target, fields):
    fields = set(fields)
    if not fields or not fields <= set(FIELDS):
        raise ValueError('Select supported metadata fields to update.')
    for key in fields - {'series'}:
        if not target[key]:
            raise ValueError(f'Calibre has no {key}; correct it before applying.')
    path, root, metadata = archive_metadata(data)
    dc, opf = NS['dc'], NS['o']
    if 'title' in fields:
        for x in list(metadata.findall('dc:title', NS)):
            remove_with_refinements(metadata, x)
        append(metadata, dc, 'title', target['title'])
        for x in metadata.findall('o:meta', NS):
            if x.get('property') == 'dcterms:title':
                x.text = target['title']
    if 'authors' in fields:
        current = author_elements(metadata)
        # An extra creator can be wrong while the actual authors are already right.
        # In that case retain their original credits and sort metadata untouched.
        if not same_authors([text(x) for x in current], target['authors']):
            sorts = {}
            for x in current:
                refined = refines(metadata, x, 'file-as')
                sorts[normal(text(x))] = text(refined[0]) if refined else x.get('{%s}file-as' % opf)
                remove_with_refinements(metadata, x)
            for name in target['authors']:
                creator = append(metadata, dc, 'creator', name)
                sort = sorts.get(normal(name))
                if root.get('version', '2').startswith('3'):
                    creator.set('id', unique_id(root, 'metadata-audit-author'))
                    append(metadata, opf, 'meta', 'aut', refines='#' + creator.get('id'), property='role', scheme='marc:relators')
                    if sort:
                        append(metadata, opf, 'meta', sort, refines='#' + creator.get('id'), property='file-as')
                else:
                    creator.set('{%s}role' % opf, 'aut')
                    if sort:
                        creator.set('{%s}file-as' % opf, sort)
        for x in metadata.findall('o:meta', NS):
            if x.get('property') == 'dcterms:creator':
                x.text = ', '.join(target['authors'])
    if 'publisher' in fields:
        for x in list(metadata.findall('dc:publisher', NS)):
            remove_with_refinements(metadata, x)
        append(metadata, dc, 'publisher', target['publisher'])
        for x in metadata.findall('o:meta', NS):
            if x.get('property') == 'dcterms:publisher':
                x.text = target['publisher']
    if 'series' in fields:
        for x in list(metadata.findall('o:meta', NS)):
            if x.get('name') in ('calibre:series', 'calibre:series_index'):
                remove_with_refinements(metadata, x)
        for x in series_elements(metadata):
            remove_with_refinements(metadata, x)
        if target['series']:
            append(metadata, opf, 'meta', name='calibre:series', content=target['series'])
            append(metadata, opf, 'meta', name='calibre:series_index', content=target['series_index'])
            if root.get('version', '2').startswith('3'):
                ident = unique_id(root, 'metadata-audit-series')
                append(metadata, opf, 'meta', target['series'], property='belongs-to-collection', id=ident)
                append(metadata, opf, 'meta', 'series', refines='#' + ident, property='collection-type')
                append(metadata, opf, 'meta', target['series_index'], refines='#' + ident, property='group-position')
    for x in metadata.findall('o:meta', NS):
        if x.get('property') == 'dcterms:modified':
            x.text = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    updated = etree.tostring(root, encoding='utf-8', xml_declaration=True)
    output = BytesIO()
    with ZipFile(BytesIO(data)) as source, ZipFile(output, 'w') as dest:
        dest.comment = source.comment
        for info in source.infolist():
            dest.writestr(info, updated if info.filename == path else source.read(info))
    answer = output.getvalue()
    # Verify every member, including covers and artwork, before replacing a format.
    with ZipFile(BytesIO(data)) as source, ZipFile(BytesIO(answer)) as dest:
        if dest.testzip() or source.namelist() != dest.namelist():
            raise ValueError('EPUB archive verification failed.')
        for name in source.namelist():
            if name != path and source.read(name) != dest.read(name):
                raise ValueError('Unexpected content change: ' + name)
    remaining = {x['field'] for x in differences(answer, target)}
    if remaining & fields:
        raise ValueError('Selected metadata fields did not pass verification.')
    return answer


def fingerprint(data):
    return sha256(data).hexdigest()


def apply_to_database(db, book_id, expected_hash, target, fields):
    """Use Calibre's API so its format cache stays consistent with the file."""
    if target_metadata(db.get_metadata(book_id)) != target:
        raise ValueError('Calibre metadata changed. Scan again before applying.')
    data = db.format(book_id, 'EPUB')
    if data is None or fingerprint(data) != expected_hash:
        raise ValueError('The EPUB changed. Scan again before applying.')
    updated = rewrite(data, target, fields)
    if not db.add_format(book_id, 'EPUB', BytesIO(updated), replace=True, run_hooks=False):
        raise ValueError('Calibre did not replace the EPUB.')
    actual = db.format(book_id, 'EPUB')
    if actual is None or fingerprint(actual) != fingerprint(updated):
        raise ValueError('The saved EPUB could not be verified.')
