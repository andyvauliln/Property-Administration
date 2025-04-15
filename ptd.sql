--
-- PostgreSQL database dump
--

-- Dumped from database version 14.13 (Ubuntu 14.13-0ubuntu0.22.04.1)
-- Dumped by pg_dump version 14.11 (Homebrew)

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: mysite_paymentype; Type: TABLE; Schema: public; Owner: postgres_user
--

CREATE TABLE public.mysite_paymentype (
    id bigint NOT NULL,
    name character varying(50) NOT NULL,
    type character varying(32) NOT NULL,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL,
    balance_sheet_name character varying(50),
    category character varying(50)
);


ALTER TABLE public.mysite_paymentype OWNER TO postgres_user;

--
-- Data for Name: mysite_paymentype; Type: TABLE DATA; Schema: public; Owner: postgres_user
--

COPY public.mysite_paymentype (id, name, type, created_at, updated_at, balance_sheet_name, category) FROM stdin;
11	Other	Out	2024-06-05 21:44:22.895345+00	2024-07-01 22:02:09.172331+00	Paybels	None Operating
12	Other	In	2024-06-09 00:58:32.192679+00	2024-07-01 22:03:32.522992+00	Receivables	None Operating
10	Profit withdraw	Out	2024-06-05 21:43:54.634843+00	2024-07-01 22:04:14.552141+00	Paybels	None Operating
7	Mortage	Out	2023-09-19 18:47:50.494189+00	2024-07-01 22:04:47.875058+00	Paybels	Operating
6	Rent	In	2023-09-19 18:47:50.494189+00	2024-07-01 22:05:15.723741+00	Receivables	Operating
5	Damage Deposit	Out	2023-09-19 18:47:50.494189+00	2024-07-01 22:06:54.062815+00	Paybels	Operating
4	Hold Deposit	In	2023-09-19 18:47:50.494189+00	2024-07-01 22:07:31.509882+00	Paybels	Operating
3	Damage Deposit	In	2023-09-19 18:47:50.494189+00	2024-07-01 22:08:08.677663+00	Receivables	Operating
2	Other	Out	2023-09-19 18:47:50.494189+00	2024-07-01 22:09:09.246621+00	Paybels	Operating
1	Other	In	2023-09-19 18:47:50.494189+00	2024-07-01 22:09:43.323374+00	Receivables	Operating
13	Parking	In	2024-07-01 22:10:17.18525+00	2024-07-01 22:10:17.185276+00	Receivables	Operating
14	Other	In	2024-07-01 22:10:45.815222+00	2024-07-01 22:10:45.81525+00	Receivables	None Operating
15	Utilities	In	2024-07-01 22:11:18.93926+00	2024-07-01 22:11:18.939284+00	Paybels	Operating
16	Internet	In	2024-07-01 22:12:00.189965+00	2024-07-01 22:12:00.189989+00	Paybels	Operating
17	Loans	In	2024-07-01 22:12:27.075366+00	2024-07-01 22:12:27.075391+00	Paybels	Operating
18	Merchant Services	Out	2024-07-01 22:12:54.18884+00	2024-07-01 22:12:54.188864+00	Paybels	Operating
19	Repairs	Out	2024-07-01 22:13:19.531745+00	2024-07-01 22:13:19.531772+00	Paybels	Operating
20	Utilities	Out	2024-07-01 22:13:45.738577+00	2024-07-01 22:13:45.738595+00	Paybels	Operating
21	Rent	Out	2024-07-01 22:14:03.709523+00	2024-07-01 22:14:03.709547+00	Paybels	Operating
22	Parking	Out	2024-07-01 22:14:38.95421+00	2024-07-01 22:14:38.954238+00	Paybels	Operating
23	Hold Deposit	Out	2024-07-01 22:15:32.299207+00	2024-07-01 22:15:32.299232+00	Paybels	Operating
\.


--
-- PostgreSQL database dump complete
--

