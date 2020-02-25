#!/usr/bin/python

import argparse
import socket
import json
import time
import api_requests
from threading import Thread, Lock
from rooms import Rooms, RoomNotFound, NotInRoom, RoomFull
from server.ygo_card_db_service import YGOCardDBService


def main_loop(tcp_port, udp_port, rooms):
    """
    Start udp and tcp server threads
    """
    lock = Lock()
    udp_server = UdpServer(udp_port, rooms, lock)
    tcp_server = TcpServer(tcp_port, rooms, lock)
    udp_server.start()
    tcp_server.start()
    is_running = True
    usage = "Simple Game Server.\n" \
        "--------------------------------------\n" \
        "list : list rooms\n" \
        "room #room_id : print room information\n" \
        "user #user_id : print user information\n" \
        "quit : quit server\n" \
        "help : show usage\n" \
        "--------------------------------------\n"

    print(usage)

    while is_running:
        cmd = input("cmd >")
        if cmd == "list":
            print("Rooms :")
            for room_id, room in rooms.rooms.items():
                print("%s - %s (%d/%d)" % (room.identifier,
                                           room.name,
                                           len(room.players),
                                           room.capacity))
        elif cmd.startswith("room "):
            try:
                id = cmd[5:]
                room = rooms.rooms[id]
                print("%s - %s (%d/%d)" % (room.identifier,
                                           room.name,
                                           len(room.players),
                                           room.capacity))
                print("Players :")
                for player in room.players:
                    print(player.identifier)
            except BaseException:
                print("Error while getting room informations")
        elif cmd.startswith("user "):
            try:
                player = rooms.players[cmd[5:]]
                print("%s : %s:%d" % (player.identifier,
                                      player.udp_addr[0],
                                      player.udp_addr[1]))
            except BaseException:
                print("Error while getting user informations")
        elif cmd == "quit" or cmd == "q":
            print("Shutting down server...")
            udp_server.is_listening = False
            tcp_server.is_listening = False
            is_running = False
        elif cmd == "help":
            print(usage)

    udp_server.join()
    tcp_server.join()


class UdpServer(Thread):
    def __init__(self, udp_port, rooms, lock):
        """
        Create a new udp server
        """
        Thread.__init__(self)
        self.rooms = rooms
        self.lock = lock
        self.is_listening = True
        self.udp_port = int(udp_port)
        self.msg = '{"success": %(success)s, "message":"%(message)s"}'

    def run(self):
        """
        Start udp server
        """
        self.sock = socket.socket(socket.AF_INET,
                                  socket.SOCK_DGRAM)
        self.sock.bind(("0.0.0.0", self.udp_port))
        self.sock.setblocking(0)
        self.sock.settimeout(5)
        while self.is_listening:
            try:
                data, address = self.sock.recvfrom(1024)
            except socket.timeout:
                continue

            try:
                data = json.loads(data)
                try:
                    identifier = data['identifier']
                except KeyError:
                    identifier = None

                try:
                    room_id = data['room_id']
                except KeyError:
                    room_id = None

                try:
                    payload = data['payload']
                except KeyError:
                    payload = None

                try:
                    action = data['action']
                except KeyError:
                    action = None

                try:
                    if room_id not in self.rooms.rooms.keys():
                        raise RoomNotFound
                    self.lock.acquire()
                    try:
                        if action == "send":
                            try:
                                self.rooms.send(identifier,
                                                room_id,
                                                payload['message'],
                                                self.sock)
                            except BaseException:
                                pass
                        elif action == "sendto":
                            try:
                                self.rooms.sendto(identifier,
                                                  room_id,
                                                  payload['recipients'],
                                                  payload['message'],
                                                  self.sock)
                            except BaseException:
                                pass
                    finally:
                        self.lock.release()
                except RoomNotFound:
                    print("Room not found")

            except KeyError:
                print("Json from %s:%s is not valid" % address)
            except ValueError:
                print("Message from %s:%s is not valid json string" % address)

        self.stop()

    def stop(self):
        """
        Stop server
        """
        self.sock.close()


class TcpServer(Thread):
    def __init__(self, tcp_port, rooms, lock):
        """
        Create a new tcp server
        """
        Thread.__init__(self)
        self.lock = lock
        self.tcp_port = int(tcp_port)
        self.rooms = rooms
        self.is_listening = True
        self.msg = '{"success": "%(success)s", "message":"%(message)s"}'

    def run(self):
        """
        Start tcp server
        """
        self.sock = socket.socket(socket.AF_INET,
                                  socket.SOCK_STREAM)
        self.sock.bind(('0.0.0.0', self.tcp_port))
        self.sock.setblocking(0)
        self.sock.settimeout(5)
        time_reference = time.time()
        self.sock.listen(1)

        while self.is_listening:

            #  Clean empty rooms
            if time_reference + 60 < time.time():
                self.rooms.remove_empty()
                time_reference = time.time()
            try:
                conn, addr = self.sock.accept()
            except socket.timeout:
                continue

            data = conn.recv(1024)
            try:
                data = json.loads(data)
                action = data['action']
                identifier = None
                try:
                    identifier = data['identifier']
                except KeyError:
                    pass  # Silently pass

                room_id = None
                try:
                    room_id = data['room_id']
                except KeyError:
                    pass  # Silently pass

                payload = None
                try:
                    payload = data['payload']
                except KeyError:
                    pass  # Silently pass
                self.lock.acquire()
                try:
                    self.route(conn,
                               addr,
                               action,
                               payload,
                               identifier,
                               room_id)
                finally:
                    self.lock.release()
            except KeyError:
                print("Json from %s:%s is not valid" % addr)
                conn.send("Json is not valid")
            except ValueError:
                print("Message from %s:%s is not valid json string" % addr)
                conn.send("Message is not a valid json string")

            conn.close()

        self.stop()

    def route(self,
              sock,
              addr,
              action,
              payload,
              identifier=None,
              room_id=None):
        """
        Route received data for processing
        """
        if action == "register":
            client = self.rooms.register(addr, int(payload))
            client.send_tcp(True, client.identifier, sock)
            return 0

        if identifier is not None:
            if identifier not in self.rooms.players.keys():
                print("Unknown identifier %s for %s:%s" %
                      (identifier, addr[0], addr[1]))
                sock.send(self.msg % {"success": "False",
                                      "message": "Unknown identifier"})
                return 0

            # Get client object
            client = self.rooms.players[identifier]

            if action == "join":
                try:
                    if payload not in self.rooms.rooms.keys():
                        raise RoomNotFound()
                    self.rooms.join(identifier, payload)
                    client.send_tcp(True, payload, sock)
                except RoomNotFound:
                    client.send_tcp(False, room_id, sock)
                except RoomFull:
                    client.send_tcp(False, room_id, sock)
            elif action == "autojoin":
                room_id = self.rooms.join(identifier)
                client.send_tcp(True, room_id, sock)
            elif action == "get_rooms":
                rooms = []
                for id_room, room in self.rooms.rooms.items():
                    rooms.append({"id": id_room,
                                  "name": room.name,
                                  "nb_players": len(room.players),
                                  "capacity": room.capacity})
                client.send_tcp(True, rooms, sock)
            elif action == "create":
                room_identifier = self.rooms.create(payload)
                self.rooms.join(client.identifier, room_identifier)
                client.send_tcp(True, room_identifier, sock)
            elif action == 'leave':
                try:
                    if room_id not in self.rooms.rooms:
                        raise RoomNotFound()
                    rooms.leave(identifier, room_id)
                    client.send_tcp(True, room_id, sock)
                except RoomNotFound:
                    client.send_tcp(False, room_id, sock)
                except NotInRoom:
                    client.send_tcp(False, room_id, sock)
            else:
                sock.send_tcp(self.msg % {"success": "False",
                                          "message": "You must register"})

    def stop(self):
        """
        Stop tcp data
        """
        self.sock.close()


if __name__ == "__main__":
    """
    Start a game server
    """
    parser = argparse.ArgumentParser(description='Simple game server')
    parser.add_argument('--tcpport', '-t',
                        dest='tcp_port',
                        help='Listening tcp port',
                        default="1234")
    parser.add_argument('--udpport', '-u',
                        dest='udp_port',
                        help='Listening udp port',
                        default="1234")
    parser.add_argument('--capacity', '-c',
                        dest='room_capacity',
                        help='Max players per room',
                        default="4")

    args = parser.parse_args()
    rooms = Rooms(int(args.room_capacity))
    main_loop(args.tcp_port, args.udp_port, rooms)

    # Comment this in and comment out the above for basic unit testing
    # card_1 = {
    #     "id": 6983839,
    #     "name": "Tornado Dragon",
    #     "type": "XYZ Monster",
    #     "desc": "2 Level 4 monsters Once per turn, during either player's turn: You can detach 1 Xyz Material from this card, then target 1 Spell/Trap Card on the field; destroy it."
    # }
    # card_2 = {
    #     "id": 34541863,
    #     "name": "\"A\" Cell Breeding Device",
    #     "type": "Spell Card",
    #     "desc": "During each of your Standby Phases, put 1 A-Counter on 1 face-up monster your opponent controls."
    # }
    # id_list = [
    #     77235086,
    #     25857246
    # ]
    DATABASE_NAME = "yugiohdb"
    COLLECTION_NAME = "card_info"
    DB_URL = "mongodb://localhost:27017"

    yugioh_db = YGOCardDBService(DATABASE_NAME, COLLECTION_NAME, DB_URL)
    print(yugioh_db)

    card_list = [card_1, card_2]

    yugioh_db.delete_card_info(card_1)
    yugioh_db.delete_card_info(card_2)
    yugioh_db.insert_card_info(card_1)

    print(yugioh_db.get_collection())

    yugioh_db.insert_cards(card_list)

    api_requests.populate_card_info_db(yugioh_db)

    yugioh_db.get_card_list(id_list)