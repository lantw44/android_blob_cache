#!/usr/bin/env python3

import argparse
import re
import sqlite3
import sys
from struct import unpack

# groups 1, 2 in the following expression are optional because there are 
# instances when the original file path is not present in the record
# meta_re = re.compile('(.+?)\\+(\\d)?\\+?(.+?\\.\\w{3,4})?\\+?(\\d+)\\+?(.+)*')
meta_re = re.compile('(.+?)\+(\d?)\+?(.+?)\+(\d{10,16})\+?(.+)*')
jpeg_header = b'\xff\xd8\xff'

def detect_codec(data: bytes, codecs: tuple = (('utf-32-le', 4),
                                               ('utf-16-le', 2))) -> tuple:
    """Attempt to detect codec of data.  Returns codec string and corresponding 
    code point length."""

    for values in codecs:
        codec, cp_len = values
        try:
            data.decode(codec)
            return codec, cp_len
        except UnicodeDecodeError:
            pass
    print(f'Codec detection error: {data}')
    sys.exit(1)


def construct_db(db: str):
    """Build empty database."""

    con = sqlite3.connect(db)
    c = con.cursor()
    c.executescript('''
    CREATE TABLE blob (
        Offset INTEGER PRIMARY KEY,
        InternalPath TEXT,
        Unk INTEGER,
        OriginalFilePath TEXT,
        TimeStamp INTEGER,
        Extra TEXT,
        Thumbnail BLOB,
        RawMetadata BLOB
    );

    CREATE TABLE blob_header (
        Offset INTEGER PRIMARY KEY,
        Key BLOB,
        Checksum INTEGER,
        BlobOffset INTEGER,
        BlobLength INTEGER,
        RawBlobHeader BLOB
    );

    CREATE VIEW Parsed_Blob_Cache AS
        SELECT
            blob.offset AS RecordOffset,
            CASE 
                WHEN length(TimeStamp) == 13 THEN datetime(TimeStamp/1000, 'unixepoch')
                ELSE datetime(TimeStamp, 'unixepoch')
            END as UTC,
            CASE 
                WHEN length(TimeStamp) == 13 THEN datetime(TimeStamp/1000, 'unixepoch', 'localtime')
                ELSE datetime(TimeStamp, 'unixepoch', 'localtime') 
            END as LocalTime,
            OriginalFilePath,
            Extra,
            InternalPath,
            Thumbnail
        FROM blob;
    ''')
    con.commit()
    return con


def main():
    parser = argparse.ArgumentParser(description='Extracts thumbnails and file \
        metadata from Android blob cache files.',
        epilog='Writes output to a SQLite database named after the cache file \
        and to the current working directory.  The sanitize option is useful \
        for sharing blob cache metadata without disclosing the content of the \
        thumbnail images. Future versions may export thumbnails to a directory \
        and create a CSV.')
    parser.add_argument('FILE', help="blob cache file to parse")
    parser.add_argument('-d', "--database", action='store_true', default=True,
        help='Write to SQLite database (default)')
    parser.add_argument('-s', "--sanitize", action='store_true', default=False,
        help="Write an blob cache file with thumbnails overwritten.")

    args = parser.parse_args()

    # If sanitizing, create a new output file
    if args.sanitize:
        fname = args.FILE.rsplit('.', 1)
        fname = fname[0] + '_sanitized.' + fname[1]
        o = open(fname, 'wb')

    if args.database and not args.sanitize:
        db_name = args.FILE + '.sqlite'
        db = construct_db(db_name)
        c = db.cursor()

    with open(args.FILE, 'rb') as f:

        # Determine file size to gracefully exit loop
        fsize = f.seek(0, 2)
        f.seek(0)

        # Read file header, quit if wrong file type provided
        file_header = f.read(4)
        if not file_header == b'\x10\x85\x24\xBD':
            print("Error: Not a blob cache file")
            sys.exit(1)
        
        # If sanitizing, write the file header to the output file
        if args.sanitize:
            o.write(file_header)

        # Read each record, structured as 20 byte header, variable length string
        # with fields separated by '+', followed by thumbnail image.
        while True:
            offset = f.tell()

            # Quit at EOF
            if offset == fsize:
                break

            # Read 20 byte record header, last 4 bytes are the blob size 
            # (metadata + thumbnail)
            rec_header = f.read(20)
            key, chksum, recoffset, payloadlen = unpack('<8s3I', rec_header)

            # Split metadata from thumbnail data using thumbnail header.  
            # Metadata is variable length and has no size indicator.
            data = f.read(payloadlen)
            metadata_end = data.find(jpeg_header)
            metadata = data[:metadata_end]
            thumbnail = data[metadata_end:]

            # If sanitizing, write the record header, metadata, and thumbnail 
            # header, zero the remaining thumbnail data
            if args.sanitize:
                o.write(rec_header)
                o.write(metadata)
                o.write(jpeg_header + b'\x00' * len(thumbnail[4:]))
                continue

            # Metadata is plus sign delimited (causing issues with paths 
            # including # plus signs).  It can be encoded utf-16-le, 
            # utf-32-le.  All utf-32 strings are ended with a utf-16 '+kar' 
            # string which is ignored in decoding.
            codec, cp_len = detect_codec(metadata[:4])
            decoded_meta = metadata.decode(codec, 'ignore')
            try:
                meta_list = meta_re.findall(decoded_meta)[0]
            except IndexError:
                print(f'Metadata in blob at Offset: {offset} not understood')
                meta_list = [None] * 5

            if args.database:  # Default for now, but CSV a future option
                apath, unk, fpath, ts, xtra = meta_list
                if not xtra:
                    xtra = None
                c.execute('''INSERT INTO blob (Offset, InternalPath,     
                    Unk, OriginalFilePath, TimeStamp, Extra, Thumbnail,
                    RawMetadata) VALUES (?,?,?,?,?,?,?,?);''', (offset, apath,
                    unk, fpath, ts, xtra,thumbnail, metadata))
                c.execute('''INSERT INTO blob_header (Offset, Key, Checksum,
                    BlobOffset, BlobLength, RawBlobHeader)
                    VALUES (?,?,?,?,?,?);''', (offset, key, chksum, 
                    recoffset, payloadlen, rec_header,))

            else:
                print(offset, meta_list, thumbnail[:4])
        if args.database and not args.sanitize:
            db.commit()
        if args.sanitize:
            o.close()

if __name__ == "__main__":
    main()
    sys.exit(0)
